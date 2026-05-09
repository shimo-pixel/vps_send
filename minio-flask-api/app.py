import os
import re
from io import BytesIO
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request
from minio import Minio
from minio.error import S3Error
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)

# 不设默认 127.0.0.1：在 Docker 里那会指向容器自己。请在 .env / compose 里显式设置，例如 minio:9000 或 host.docker.internal:9000
_raw_endpoint = os.environ.get("MINIO_ENDPOINT", "").strip().replace("https://", "").replace("http://", "")
if not _raw_endpoint:
    raise RuntimeError(
        "MINIO_ENDPOINT 未设置。Docker 与 MinIO 同 compose 时一般为 minio:9000；"
        "本机直接运行 Flask 且 MinIO 在本机时为 127.0.0.1:9000；"
        "仅 API 在容器、MinIO 在宿主机 Linux 时常用 host.docker.internal:9000（compose 需 extra_hosts）。"
    )
_ENDPOINT = _raw_endpoint
_ACCESS = os.environ["MINIO_ACCESS_KEY"]
_SECRET = os.environ["MINIO_SECRET_KEY"]
_SECURE = os.environ.get("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
_BUCKET = os.environ.get("MINIO_BUCKET", "uploads")

_client: Optional[Minio] = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            _ENDPOINT,
            access_key=_ACCESS,
            secret_key=_SECRET,
            secure=_SECURE,
        )
    return _client


def ensure_bucket() -> None:
    c = get_client()
    if not c.bucket_exists(_BUCKET):
        c.make_bucket(_BUCKET)


def safe_object_name(name: str) -> str:
    name = name.strip().lstrip("/")
    if not name or ".." in name or name.startswith("."):
        abort(400, description="invalid object name")
    if not re.match(r"^[\w\-./]+$", name, re.UNICODE):
        abort(400, description="object name has invalid characters")
    return name


@app.route("/health", methods=["GET"])
def health():
    out = {"ok": True, "bucket": _BUCKET, "minio_endpoint": _ENDPOINT}
    try:
        get_client().bucket_exists(_BUCKET)
        out["minio"] = "ok"
    except Exception as e:
        out["ok"] = False
        out["minio"] = "error"
        out["minio_error"] = f"{type(e).__name__}: {e}"
    return out


@app.route("/upload", methods=["POST"])
def upload():
    """
    multipart/form-data，字段名 file；可选 form 字段 object_name（对象在桶里的路径/文件名）。
    未传 object_name 时使用上传文件的原始文件名（经 secure_filename）。
    """
    if "file" not in request.files:
        abort(400, description="missing file field")
    f = request.files["file"]
    if not f.filename:
        abort(400, description="empty filename")

    raw_name = (request.form.get("object_name") or f.filename).strip()
    object_name = safe_object_name(raw_name)
    parts = [p for p in object_name.split("/") if p]
    if not parts:
        abort(400, description="invalid object name")
    parts[-1] = secure_filename(parts[-1]) or "file"
    object_name = "/".join(parts)

    data = f.read()
    length = len(data)
    try:
        ensure_bucket()
        c = get_client()
        c.put_object(
            _BUCKET,
            object_name,
            BytesIO(data),
            length,
            content_type=f.mimetype or "application/octet-stream",
        )
    except HTTPException:
        raise
    except S3Error as e:
        return (
            jsonify(
                {
                    "error": "s3",
                    "code": getattr(e, "code", None),
                    "message": str(e),
                }
            ),
            502,
        )
    except Exception as e:
        return (
            jsonify(
                {
                    "error": "storage_unreachable",
                    "message": str(e),
                    "hint": "确认 MinIO 已启动且 MINIO_ENDPOINT 对当前进程可达（本机 python 用 127.0.0.1:9000；Docker 内用 minio:9000）。可先 GET /health 看 minio 字段。",
                }
            ),
            503,
        )

    return {
        "bucket": _BUCKET,
        "object": object_name,
        "size": length,
        "content_type": f.mimetype or "application/octet-stream",
    }


@app.route("/objects/<path:object_name>", methods=["GET"])
def get_object(object_name: str):
    """按对象键下载；可选 ?disposition=attachment 强制附件下载。"""
    key = safe_object_name(object_name)
    try:
        ensure_bucket()
        c = get_client()
        try:
            obj = c.get_object(_BUCKET, key)
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchBucket"):
                abort(404)
            raise
    except HTTPException:
        raise
    except S3Error as e:
        return (
            jsonify({"error": "s3", "code": getattr(e, "code", None), "message": str(e)}),
            502,
        )
    except Exception as e:
        return (
            jsonify(
                {
                    "error": "storage_unreachable",
                    "message": str(e),
                    "hint": "GET /health 检查 minio 是否 ok",
                }
            ),
            503,
        )

    data = obj.read()
    ct = obj.headers.get("Content-Type", "application/octet-stream")
    dl = request.args.get("disposition", "").lower() == "attachment"
    headers = {}
    if dl:
        fname = key.rsplit("/", 1)[-1]
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'

    return Response(data, mimetype=ct, headers=headers)


def main():
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
