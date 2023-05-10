"""Classes/utilities for support of a dandiset"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import Optional, TypeVar

from dandischema.models import get_schema_version

from . import get_logger
from .consts import dandiset_metadata_file
from .files import DandisetMetadataFile, LocalAsset, dandi_file, find_dandi_files
from .utils import find_parent_directory_containing, yaml_dump, yaml_load

lgr = get_logger()

D = TypeVar("D", bound="Dandiset")


class Dandiset:
    """A prototype class for all things dandiset"""

    __slots__ = ["metadata", "path", "path_obj", "_metadata_file_obj"]

    def __init__(
        self,
        path: str | Path,
        allow_empty: bool = False,
        schema_version: Optional[str] = None,
    ) -> None:
        if schema_version is not None:
            current_version = get_schema_version()
            if schema_version != current_version:
                raise ValueError(
                    f"Unsupported schema version: {schema_version}; expected {current_version}"
                )
        self.path = str(path)
        self.path_obj = Path(path)
        if not allow_empty and not (self.path_obj / dandiset_metadata_file).exists():
            raise ValueError(f"No Dandiset at {path}")
        self.metadata: Optional[dict] = None
        self._metadata_file_obj = self.path_obj / dandiset_metadata_file
        self._load_metadata()

    @classmethod
    def find(cls: type[D], path: str | Path | None) -> Optional[D]:
        """Find a dandiset possibly pointing to a directory within it"""
        dandiset_path = find_parent_directory_containing(dandiset_metadata_file, path)
        if dandiset_path is not None:
            return cls(dandiset_path)
        return None

    def _load_metadata(self) -> None:
        try:
            with self._metadata_file_obj.open() as f:
                # TODO it would cast 000001 if not explicitly string into
                # an int -- we should prevent it... probably with some custom loader
                self.metadata = yaml_load(f, typ="safe")
        except FileNotFoundError:
            self.metadata = None

    @classmethod
    def get_dandiset_record(cls, meta: dict) -> str:
        dandiset_identifier = cls._get_identifier(meta)
        if not dandiset_identifier:
            lgr.warning("No identifier for a dandiset was provided in %s", meta)
            obtain_msg = ""
        else:
            obtain_msg = (
                " edited online at https://dandiarchive.org/dandiset/"
                f"{dandiset_identifier}\n# and"
            )
        header = f"""\
# DO NOT EDIT THIS FILE LOCALLY. ALL LOCAL UPDATES WILL BE LOST.
# It can be{obtain_msg} obtained from the dandiarchive.
"""
        yaml_rec = yaml_dump(meta)
        return header + yaml_rec

    def update_metadata(self, meta: dict) -> None:
        """Update existing metadata record in dandiset.yaml"""
        if not meta:
            lgr.debug("No updates to metadata, returning")
            return

        try:
            with self._metadata_file_obj.open() as f:
                rec = yaml_load(f, typ="safe")
        except FileNotFoundError:
            rec = {}

        # TODO: decide howto and properly do updates to nested structures if
        # possible.  Otherwise limit to the fields we know could be modified
        # locally
        rec.update(meta)

        self._metadata_file_obj.write_text(self.get_dandiset_record(rec))

        # and reload now by a pure yaml
        self._load_metadata()

    @staticmethod
    def _get_identifier(metadata: dict) -> Optional[str]:
        """Given a metadata record, determine identifier"""
        # ATM since we have dichotomy in dandiset metadata schema from drafts
        # and from published versions, we will just test both locations
        id_ = metadata.get("dandiset", {}).get("identifier")
        if id_:
            # very old but might still be present... TODO: API-migration-remove
            lgr.debug("Found identifier %s in 'dandiset.identifier'", id_)

        if not id_ and "identifier" in metadata:
            # girder-based, used before migration to API  TODO: API-migration-remove
            id_ = metadata["identifier"]
            lgr.debug("Found identifier %s in top level 'identifier'", str(id_))

        if isinstance(id_, dict):
            # New formalized model, but see below DANDI: way
            # TODO: add schemaVersion handling but only after we have them provided
            # in all metadata records from dandi-api server
            if id_.get("propertyID") != "DANDI":
                raise ValueError(
                    f"Got following identifier record when was expecting a record "
                    f"with 'propertyID: DANDI': {id_}"
                )
            id_ = str(id_.get("value", ""))
        elif id_ is not None:
            assert isinstance(id_, str)
            if id_.startswith("DANDI:"):
                # result of https://github.com/dandi/dandi-cli/pull/348 which
                id_ = id_[len("DANDI:") :]

        assert id_ is None or isinstance(id_, str)
        return id_

    @property
    def identifier(self) -> str:
        if self.metadata is None:
            raise ValueError("No metadata record found in Dandiset")
        id_ = self._get_identifier(self.metadata)
        if not id_:
            raise ValueError(
                f"Found no dandiset.identifier in metadata record: {self.metadata}"
            )
        return id_

    def assets(self, allow_all: bool = False) -> AssetView:
        data = {}
        for df in find_dandi_files(
            self.path, dandiset_path=self.path, allow_all=allow_all
        ):
            if isinstance(df, DandisetMetadataFile):
                continue
            assert isinstance(df, LocalAsset)
            data[PurePosixPath(df.path)] = df
        return AssetView(data)

    def metadata_file(self) -> DandisetMetadataFile:
        df = dandi_file(self._metadata_file_obj, dandiset_path=self.path)
        assert isinstance(df, DandisetMetadataFile)
        return df


@dataclass
class AssetView:
    """
    A collection of all assets in a local Dandiset, used to ensure that
    `BIDSDatasetDescriptionAsset` objects are stored and remain alive while
    working with only a subset of the files in a Dandiset.
    """

    data: dict[PurePosixPath, LocalAsset]

    def __iter__(self) -> Iterator[LocalAsset]:
        return iter(self.data.values())

    def under_paths(self, paths: Iterable[str | Path]) -> Iterator[LocalAsset]:
        # The given paths must be relative to the Dandiset root.
        ppaths = [PurePosixPath(p) for p in paths]
        for p, df in self.data.items():
            if any(is_relative_to(p, pp) for pp in ppaths):
                yield df


def is_relative_to(p1: PurePath, p2: PurePath) -> bool:
    # This also returns true when p1 == p2, which we want to happen.
    # This can be replaced with PurePath.is_relative_to() once we drop support
    #   for Python 3.8.
    try:
        p1.relative_to(p2)
    except ValueError:
        return False
    else:
        return True
