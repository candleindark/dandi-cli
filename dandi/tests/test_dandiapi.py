from collections import deque
import os.path
from shutil import rmtree

from ..consts import dandiset_metadata_file
from ..dandiapi import DandiAPIClient
from ..download import download
from ..upload import upload


def test_upload(local_dandi_api, simple1_nwb, tmp_path):
    client = DandiAPIClient(
        api_url=local_dandi_api["instance"].api, token=local_dandi_api["api_key"]
    )
    with client.session():
        r = client.create_dandiset(name="Upload Test", metadata={})
        dandiset_id = r["identifier"]
        client.upload(dandiset_id, "draft", "testing/simple1.nwb", {}, simple1_nwb)
        asset, = client.get_dandiset_assets(dandiset_id, "draft")
        assert asset["path"] == "testing/simple1.nwb"
        client.download_assets_directory(dandiset_id, "draft", "", tmp_path)
        p, = [p for p in tmp_path.glob("**/*") if p.is_file()]
        assert p == tmp_path / "testing" / "simple1.nwb"
        assert p.stat().st_size == os.path.getsize(simple1_nwb)


def test_publish_and_manipulate(local_dandi_api, mocker, monkeypatch, tmp_path):
    client = DandiAPIClient(
        api_url=local_dandi_api["instance"].api, token=local_dandi_api["api_key"]
    )
    dandiset_id = client.create_dandiset("Test Dandiset", {})["identifier"]
    upload_dir = tmp_path / "upload"
    upload_dir.mkdir()
    (upload_dir / dandiset_metadata_file).write_text(f"identifier: '{dandiset_id}'\n")
    (upload_dir / "subdir").mkdir()
    (upload_dir / "subdir" / "file.txt").write_text("This is test text.\n")
    monkeypatch.chdir(upload_dir)
    monkeypatch.setenv("DANDI_API_KEY", local_dandi_api["api_key"])
    upload(
        paths=[],
        dandi_instance=local_dandi_api["instance_id"],
        devel_debug=True,
        allow_any_path=True,
        validation="skip",
    )

    version_id = client.publish_version(dandiset_id, "draft")["version"]

    download_dir = tmp_path / "download"
    download_dir.mkdir()

    def downloaded_files():
        dirs = deque([download_dir])
        while dirs:
            d = dirs.popleft()
            for p in d.iterdir():
                if p.is_dir():
                    dirs.append(p)
                else:
                    yield p

    dandiset_yaml = download_dir / dandiset_id / dandiset_metadata_file
    file_in_version = download_dir / dandiset_id / "subdir" / "file.txt"

    download(
        f"{local_dandi_api['instance'].api}/dandisets/{dandiset_id}/versions/{version_id}",
        download_dir,
    )
    assert sorted(downloaded_files()) == [dandiset_yaml, file_in_version]
    assert file_in_version.read_text() == "This is test text.\n"

    (upload_dir / "subdir" / "file.txt").write_text("This is different text.\n")
    upload(
        paths=[],
        dandi_instance=local_dandi_api["instance_id"],
        devel_debug=True,
        allow_any_path=True,
        validation="skip",
    )
    rmtree(download_dir / dandiset_id)
    download(
        f"{local_dandi_api['instance'].api}/dandisets/{dandiset_id}/versions/{version_id}",
        download_dir,
    )
    assert sorted(downloaded_files()) == [dandiset_yaml, file_in_version]
    assert file_in_version.read_text() == "This is test text.\n"

    (upload_dir / "subdir" / "file2.txt").write_text("This is more text.\n")
    upload(
        paths=[],
        dandi_instance=local_dandi_api["instance_id"],
        devel_debug=True,
        allow_any_path=True,
        validation="skip",
    )

    rmtree(download_dir / dandiset_id)
    download(
        f"{local_dandi_api['instance'].api}/dandisets/{dandiset_id}/versions/draft",
        download_dir,
    )
    assert sorted(downloaded_files()) == [
        dandiset_yaml,
        file_in_version,
        file_in_version.with_name("file2.txt"),
    ]
    assert file_in_version.read_text() == "This is different text.\n"
    assert file_in_version.with_name("file2.txt").read_text() == "This is more text.\n"

    rmtree(download_dir / dandiset_id)
    download(
        f"{local_dandi_api['instance'].api}/dandisets/{dandiset_id}/versions/{version_id}",
        download_dir,
    )
    assert sorted(downloaded_files()) == [dandiset_yaml, file_in_version]
    assert file_in_version.read_text() == "This is test text.\n"

    client.delete_asset_bypath(dandiset_id, "draft", "subdir/file.txt")

    rmtree(download_dir / dandiset_id)
    download(
        f"{local_dandi_api['instance'].api}/dandisets/{dandiset_id}/versions/draft",
        download_dir,
    )
    assert sorted(downloaded_files()) == [
        dandiset_yaml,
        file_in_version.with_name("file2.txt"),
    ]
    assert file_in_version.with_name("file2.txt").read_text() == "This is more text.\n"

    rmtree(download_dir / dandiset_id)
    download(
        f"{local_dandi_api['instance'].api}/dandisets/{dandiset_id}/versions/{version_id}",
        download_dir,
    )
    assert sorted(downloaded_files()) == [dandiset_yaml, file_in_version]
    assert file_in_version.read_text() == "This is test text.\n"


def test_get_asset_include_metadata(local_dandi_api, simple1_nwb, tmp_path):
    client = DandiAPIClient(
        api_url=local_dandi_api["instance"].api, token=local_dandi_api["api_key"]
    )
    with client.session():
        r = client.create_dandiset(name="Include Metadata Test", metadata={})
        dandiset_id = r["identifier"]
        client.upload(
            dandiset_id, "draft", "testing/simple1.nwb", {"foo": "bar"}, simple1_nwb
        )

        asset, = client.get_dandiset_assets(dandiset_id, "draft")
        assert "metadata" not in asset
        asset, = client.get_dandiset_assets(dandiset_id, "draft", include_metadata=True)
        assert asset["metadata"] == {"foo": "bar"}

        _, (asset,) = client.get_dandiset_and_assets(dandiset_id, "draft")
        assert "metadata" not in asset
        _, (asset,) = client.get_dandiset_and_assets(
            dandiset_id, "draft", include_metadata=True
        )
        assert asset["metadata"] == {"foo": "bar"}

        asset = client.get_asset_bypath(dandiset_id, "draft", "testing/simple1.nwb")
        assert asset is not None
        assert "metadata" not in asset
        asset = client.get_asset_bypath(
            dandiset_id, "draft", "testing/simple1.nwb", include_metadata=True
        )
        assert asset is not None
        assert asset["metadata"] == {"foo": "bar"}
