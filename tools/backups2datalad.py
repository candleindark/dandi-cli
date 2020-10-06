__requires__ = ["boto3", "click", "dandi", "datalad", "requests"]

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse, urlunparse

import boto3
from botocore import UNSIGNED
from botocore.client import Config
import click
from dandi import girder
from dandi.consts import dandiset_metadata_file
from dandi.dandiarchive import navigate_url
from dandi.dandiset import Dandiset
from dandi.utils import fromisoformat, get_instance
from datalad.api import Dataset
import requests

log = logging.getLogger(Path(sys.argv[0]).name)


@click.command()
@click.option("--gh-login")
@click.option("--gh-org", help="GitHub organization to create repositories under")
@click.option("--gh-password")
@click.option("-i", "--ignore-errors", is_flag=True)
@click.argument("assetstore", type=click.Path(exists=True, file_okay=False))
@click.argument("target", type=click.Path(file_okay=False))
def main(assetstore, target, ignore_errors, gh_org, gh_login, gh_password):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-8s] %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        level=logging.INFO,
        force=True,  # Override dandi's settings
    )
    DatasetInstantiator(
        assetstore_path=Path(assetstore),
        target_path=Path(target),
        ignore_errors=ignore_errors,
        gh_org=gh_org,
        gh_login=gh_login,
        gh_password=gh_password,
    ).run()


class DatasetInstantiator:
    def __init__(
        self,
        assetstore_path: Path,
        target_path: Path,
        ignore_errors=False,
        gh_org=None,
        gh_login=None,
        gh_password=None,
    ):
        self.assetstore_path = assetstore_path
        self.target_path = target_path
        self.ignore_errors = ignore_errors
        self.gh_org = gh_org
        self.gh_login = gh_login
        self.gh_password = gh_password
        self.session = None
        self._s3client = None

    def run(self):
        self.target_path.mkdir(parents=True, exist_ok=True)
        with requests.Session() as self.session:
            for did in self.get_dandiset_ids():
                log.info("Syncing Dandiset %s", did)
                ds = Dataset(str(self.target_path / did))
                if not ds.is_installed():
                    log.info("Creating Datalad dataset")
                    ds.create(cfg_proc="text2git")
                    ds.config.set("annex.backends", "SHA256E", where="local")
                gitattrs = ds.pathobj / ".gitattributes"
                gitattrs.write_text(gitattrs.read_text().replace("MD5E", "SHA256E"))
                if self.sync_dataset(did, ds):
                    log.info("Creating GitHub sibling for %s", ds.pathobj.name)
                    ds.create_sibling_github(
                        reponame=ds.pathobj.name,
                        existing="skip",
                        name="github",
                        github_organization=self.gh_org,
                        github_login=self.gh_login,
                        github_passwd=self.gh_password,
                    )
                    log.info("Pushing to sibling")
                    ds.push(to="github")

    def sync_dataset(self, dandiset_id, ds):
        def get_annex_hash(file):
            return ds.repo.get_file_key(file).split("-")[-1].partition(".")[0]

        latest_mtime = None
        with navigate_url(f"https://dandiarchive.org/dandiset/{dandiset_id}/draft") as (
            _,
            dandiset,
            assets,
        ):
            dsdir = ds.pathobj
            log.info("Updating metadata file")
            try:
                (dsdir / dandiset_metadata_file).unlink()
            except FileNotFoundError:
                pass
            metadata = dandiset.get("metadata", {})
            Dandiset(dsdir, allow_empty=True).update_metadata(metadata)
            ds.repo.add([dandiset_metadata_file])
            local_assets = set(
                f
                for d in dsdir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                for f in d.iterdir()
            )
            for a in assets:
                log.info("Syncing asset %s", a["path"])
                gid = a["girder"]["id"]
                dandi_hash = a.get("sha256")
                if dandi_hash is None:
                    log.warning("Asset metadata does not include sha256 hash")
                mtime = fromisoformat(a["attrs"]["mtime"])
                bucket_url = self.get_file_bucket_url(gid)
                dest = dsdir / a["path"].lstrip("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                local_assets.discard(dest)
                deststr = str(dest.relative_to(dsdir))
                if not dest.exists():
                    log.info("Asset not in dataset; will copy")
                    to_update = True
                elif dandi_hash is not None:
                    if dandi_hash == get_annex_hash(deststr):
                        log.info(
                            "Asset in dataset, and hash shows no modification; will not update"
                        )
                        to_update = False
                    else:
                        log.info(
                            "Asset in dataset, and hash shows modification; will update"
                        )
                        to_update = True
                else:
                    stat = dest.stat()
                    if (
                        stat.st_size == a["attrs"]["size"]
                        and stat.st_mtime == mtime.timestamp()
                    ):
                        log.info(
                            "Asset in dataset, and size & mtime match; will not update"
                        )
                        to_update = False
                    else:
                        log.info(
                            "Asset in dataset, and size & mtime do not match; will update"
                        )
                        to_update = True
                if to_update:
                    src = self.assetstore_path / urlparse(bucket_url).path.lstrip("/")
                    if src.exists():
                        try:
                            self.mklink(src, dest)
                        except Exception:
                            if self.ignore_errors:
                                log.warning("cp command failed; ignoring")
                                continue
                            else:
                                raise
                        log.info("Adding asset to dataset")
                        ds.repo.add([deststr])
                        log.info("Adding URL %s to asset", bucket_url)
                        ds.repo.add_url_to_file(deststr, bucket_url)
                    else:
                        log.info(
                            "Asset not found in assetstore; downloading from %s",
                            bucket_url,
                        )
                        ds.download_url(urls=bucket_url, path=deststr)
                    if latest_mtime is None or mtime > latest_mtime:
                        latest_mtime = mtime
                if dandi_hash is not None:
                    annex_key = get_annex_hash(deststr)
                    if dandi_hash != annex_key:
                        raise RuntimeError(
                            f"Hash mismatch for {deststr.relative_to(self.target_path)}!"
                            f"  Dandiarchive reports {dandi_hash},"
                            f" datalad reports {annex_key}"
                        )
            for a in local_assets:
                astr = str(a.relative_to(dsdir))
                log.info(
                    "Asset %s is in dataset but not in Dandiarchive; deleting", astr
                )
                ds.repo.remove([astr])
        log.info("Commiting changes")
        with custom_commit_date(latest_mtime):
            res = ds.save(message="Ran backups2datalad.py")
        try:
            saveres, = [r for r in res if r["action"] == "save"]
        except Exception as e:
            log.error(
                'Error while attempting to determine status of "save" operation: %s: %s',
                type(e).__name__,
                str(e),
            )
        else:
            return saveres["status"] != "notneeded"

    @staticmethod
    def get_dandiset_ids():
        dandi_instance = get_instance("dandi")
        client = girder.get_client(dandi_instance.girder, authenticate=False)
        offset = 0
        per_page = 50
        while True:
            dandisets = client.get(
                "dandi", parameters={"limit": per_page, "offset": offset}
            )
            if not dandisets:
                break
            for d in dandisets:
                yield d["meta"]["dandiset"]["identifier"]
            offset += len(dandisets)

    def get_file_bucket_url(self, girder_id):
        r = (self.session or requests).head(
            f"https://girder.dandiarchive.org/api/v1/file/{girder_id}/download"
        )
        r.raise_for_status()
        urlbits = urlparse(r.headers["Location"])
        s3meta = self.s3client.get_object(
            Bucket="dandiarchive", Key=urlbits.path.lstrip("/")
        )
        return urlunparse(urlbits._replace(query=f"versionId={s3meta['VersionId']}"))

    @property
    def s3client(self):
        if self._s3client is None:
            self._s3client = boto3.client(
                "s3", config=Config(signature_version=UNSIGNED)
            )
        return self._s3client

    @staticmethod
    def mklink(src, dest):
        log.info("cp %s -> %s", src, dest)
        subprocess.run(
            [
                "cp",
                "-L",
                "--reflink=always",
                "--remove-destination",
                str(src),
                str(dest),
            ],
            check=True,
        )


@contextmanager
def custom_commit_date(dt):
    if dt is not None:
        with envvar_set("GIT_AUTHOR_DATE", str(dt)):
            with envvar_set("GIT_COMMITTER_DATE", str(dt)):
                yield
    else:
        yield


@contextmanager
def envvar_set(name, value):
    oldvalue = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if oldvalue is not None:
            os.environ[name] = oldvalue
        else:
            del os.environ[name]


if __name__ == "__main__":
    main()
