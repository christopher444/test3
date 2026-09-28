from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Protocol

import boto3
from app.aws import boto_config

from app.config import Settings, settings


class ObjectStore(Protocol):
    def put_file(self, source: str | Path, key: str) -> str: ...
    def download_file(self, key: str, destination: str | Path) -> Path: ...
    def put_json(self, key: str, payload: object) -> str: ...
    def get_json(self, key: str) -> object: ...


class LocalObjectStore:
    """Filesystem-only object store for lightweight local runs.

    This does not call or emulate Amazon S3. LocalEmu-backed runs use
    ``S3ObjectStore`` with ``AWS_ENDPOINT_URL`` pointing at LocalEmu.
    """

    def __init__(self, root: str | Path = "./runtime/objects"):        
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_file(self, source: str | Path, key: str) -> str:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return str(destination)

    def download_file(self, key: str, destination: str | Path) -> Path:
        source = self._path(key)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    def put_json(self, key: str, payload: object) -> str:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        return str(destination)

    def get_json(self, key: str) -> object:
        return json.loads(self._path(key).read_text(encoding="utf-8"))


class S3ObjectStore:
    def __init__(self, cfg: Settings = settings):
        self.cfg = cfg
        self.client = boto3.client(
            "s3",
            region_name=cfg.aws_region,
            endpoint_url=cfg.aws_endpoint_url,
            config=boto_config(cfg),            
        )

    def _extra_args(self, content_type: str | None = None) -> dict:
        args: dict[str, str] = {}
        if content_type:
            args["ContentType"] = content_type
        if self.cfg.kms_key_id:
            args.update({"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.cfg.kms_key_id})
        return args

    def put_file(self, source: str | Path, key: str) -> str:
        self.client.upload_file(
            str(source),
            self.cfg.s3_bucket,
            key,
            ExtraArgs=self._extra_args("text/csv"),
        )
        return f"s3://{self.cfg.s3_bucket}/{key}"

    def download_file(self, key: str, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.cfg.s3_bucket, key, str(destination))
        return destination

    def put_json(self, key: str, payload: object) -> str:
        kwargs = self._extra_args("application/json")
        self.client.put_object(
            Bucket=self.cfg.s3_bucket,
            Key=key,
            Body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            **kwargs,
        )
        return f"s3://{self.cfg.s3_bucket}/{key}"

    def get_json(self, key: str) -> object:
        response = self.client.get_object(Bucket=self.cfg.s3_bucket, Key=key)
        return json.loads(response["Body"].read())


def object_store(cfg: Settings = settings) -> ObjectStore:
    if cfg.storage_backend == "s3":
        return S3ObjectStore(cfg)
    if cfg.storage_backend == "filesystem":
        return LocalObjectStore(Path(cfg.runtime_dir) / "objects")
    raise ValueError(f"unsupported STORAGE_BACKEND: {cfg.storage_backend}")
