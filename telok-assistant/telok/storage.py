import hashlib
import io

from PIL import Image

from telok.db import transaction, uid
from telok.models import Asset
from telok.settings import settings


def put(project_id: str, data: bytes, mime: str, info: dict | None = None) -> str:
    if len(data) > 50_000_000:
        raise ValueError("Файл превышает 50 MB.")
    digest = hashlib.sha256(data).hexdigest()
    ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "video/mp4": "mp4",
        "audio/wav": "wav",
        "audio/mpeg": "mp3",
    }.get(mime)
    if not ext:
        raise ValueError("Неподдерживаемый MIME.")
    metadata = dict(info or {})
    if mime.startswith("image/"):
        with Image.open(io.BytesIO(data)) as image:
            if image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[mime]:
                raise ValueError("Declared MIME не совпадает с форматом файла.")
            if image.width * image.height > 40_000_000 or max(image.size) > 8192:
                raise ValueError("Изображение превышает допустимые dimensions.")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            metadata.update(width=image.width, height=image.height)
    key = f"{project_id}/{uid()}/{digest}.{ext}"
    cfg = settings()
    if cfg.storage == "s3":
        client = s3()
        response = client.put_object(Bucket=cfg.s3_bucket, Key=key, Body=data, ContentType=mime)
        metadata["object_version"] = response.get("VersionId", "")
    else:
        path = cfg.data_dir / "assets" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(data)
    with transaction() as session:
        asset = Asset(project_id=project_id, key=key, sha256=digest, mime=mime, size=len(data), info=metadata)
        session.add(asset)
        session.flush()
        return asset.id


def s3():
    import boto3

    cfg = settings()
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint or None,
        region_name=cfg.s3_region,
        aws_access_key_id=cfg.s3_access_key.get_secret_value() or None,
        aws_secret_access_key=cfg.s3_secret_key.get_secret_value() or None,
    )


def read(asset_id: str, project_id: str | None = None) -> tuple[bytes, dict]:
    with transaction() as session:
        asset = session.get(Asset, asset_id)
        if not asset or (project_id and asset.project_id != project_id):
            raise ValueError("Файл не найден в проекте.")
        info = {
            "id": asset.id,
            "key": asset.key,
            "sha256": asset.sha256,
            "mime": asset.mime,
            "info": asset.info,
        }
    cfg = settings()
    if cfg.storage == "s3":
        args = {"Bucket": cfg.s3_bucket, "Key": info["key"]}
        if info["info"].get("object_version"):
            args["VersionId"] = info["info"]["object_version"]
        data = s3().get_object(**args)["Body"].read(50_000_001)
    else:
        root = (cfg.data_dir / "assets").resolve()
        path = (root / info["key"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Некорректный путь объекта.")
        data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise ValueError("Checksum файла не совпадает с принятой версией.")
    return data, info
