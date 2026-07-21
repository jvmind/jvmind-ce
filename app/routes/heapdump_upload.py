"""Heapdump 分块上传路由。

设计原则（对齐 IMPLEMENTATION_GUIDE §3.1）：
- 流式写盘：async for chunk in request.stream() 边读边写边算 MD5，O(1) 内存。
- 断点续传：分块独立落盘 <uid>/<index>.part，客户端可查已收分块跳过重传。
- complete 时校验 hprof 魔数（JAVA PROFILE 1.0.x 或 gzip 1f 8b），早失败。
- 配额：complete 成功后扣 file_upload_count，失败不回滚（已占存储）。
- report_id 前缀 hd_，dump_dir = HEAPDUMP_STORAGE_ROOT / <report_id>/。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid as _uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.core import helpers, state
from app.services.audit import log_audit

router = APIRouter(tags=["heapdump-upload"])
_logger = logging.getLogger(__name__)

# 8 MiB 默认分块大小（前端建议值，服务端不强制）
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024

# 上传会话临时目录（分块落盘位置）
_UPLOAD_TMP_ROOT = Path(os.getenv("HEAPDUMP_UPLOAD_TMP", tempfile.gettempdir())) / "heapdump-chunks"

# NFS 上 hprof + index 存储根目录（Web/Worker/query-service 共享挂载）
_STORAGE_ROOT = Path(os.getenv("HEAPDUMP_STORAGE_ROOT", "./data/heapdumps"))

# P1 (2026-07-09 code review): parse_args 白名单。
#
# 客户端可传的解析参数只接受以下键：
#   - discard_ratio: 解析时跳过 OQL result set 行数的比例
#   - keep_unreachable: 是否保留不可达对象
#
# 显式拒绝的键：
#   - xmx / mat_home / hprof_kind / hprof_file：服务端设置 / 任意路径风险
#   - 任何其他键：忽略（已在 complete_upload 内过滤 + audit log）
_PARSE_ARGS_ALLOWLIST = frozenset({"discard_ratio", "keep_unreachable"})

def _upload_dir(upload_id: str) -> Path:
    return _UPLOAD_TMP_ROOT / upload_id


def _dump_dir(report_id: str) -> Path:
    return _STORAGE_ROOT / report_id


def _sanitize_filename(fname: str) -> str:
    """去掉路径分量，长度限制 255；防路径遍历。"""
    fname = (fname or "heapdump.hprof").replace("\\", "/").split("/")[-1]
    if len(fname) > 255:
        raise HTTPException(400, "文件名过长，最大 255 字符 / Filename too long, max 255 characters")
    return fname


def _detect_hprof_kind(head: bytes) -> str:
    """按魔数判定 hprof 类型，返回 'plain' 或 'gzip'；无效则抛 400。

    - 标准 hprof: 前 32 字节含 b"JAVA PROFILE"（形如 "JAVA PROFILE 1.0.2\\0"）
    - gzip 压缩 hprof: 前两字节 b"\\x1f\\x8b"，MAT 认扩展名 .hprof.gz 直接解析

    校验放在合并的第一块数据上：gzip 的 hprof 解压前是看不到 JAVA PROFILE 的，
    所以先看 gzip 魔数；否则要求见到 JAVA PROFILE。
    """
    if len(head) >= 2 and head[:2] == b"\x1f\x8b":
        return "gzip"
    if b"JAVA PROFILE" in head[:32]:
        return "plain"
    raise HTTPException(
        400,
        "文件不是有效的 Java heap dump (缺少 JAVA PROFILE 或 gzip 魔数) / "
        "File is not a valid Java heap dump (missing JAVA PROFILE or gzip magic)",
    )


# ---------- 端点 ----------


@router.post("/api/heapdump-reports/uploads")
async def create_upload_session(request: Request):
    """创建上传会话，返回 {upload_id, chunk_size}。前端按序 PUT 分块。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    uid = _uuid.uuid4().hex
    d = _upload_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    # 记录会话元数据（owner + 创建时间），限制其他人访问自己的上传
    meta = {"user_id": user_id, "created_at": helpers._now_str()}
    (d / ".meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"upload_id": uid, "chunk_size": DEFAULT_CHUNK_SIZE}


def _load_session_meta(uid: str) -> dict:
    d = _upload_dir(uid)
    if not d.exists():
        raise HTTPException(404, "上传会话不存在 / Upload session not found")
    meta_path = d / ".meta.json"
    if not meta_path.exists():
        raise HTTPException(410, "上传会话已损坏或已清理 / Upload session corrupted or cleaned")
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(410, "上传会话元数据不可读 / Upload session metadata unreadable")


def _check_session_owner(uid: str, user_id: str) -> dict:
    meta = _load_session_meta(uid)
    if meta.get("user_id") != user_id:
        raise HTTPException(403, "无权访问该上传会话 / Not the owner of this upload session")
    return meta


@router.put("/api/heapdump-reports/uploads/{uid}/chunks/{index}")
async def upload_chunk(request: Request, uid: str, index: int):
    """接收单个分块（PUT，body 是二进制）。流式写盘 + Content-MD5 校验。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    _check_session_owner(uid, user_id)
    if index < 0:
        raise HTTPException(400, "分块索引不合法 / Invalid chunk index")

    d = _upload_dir(uid)
    chunk_path = d / f"{index}.part"
    tmp_path = d / f"{index}.part.tmp"

    expected_md5 = (request.headers.get("content-md5") or "").strip().lower()

    md5 = hashlib.md5()
    total = 0
    try:
        with open(tmp_path, "wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                md5.update(chunk)
                f.write(chunk)
                total += len(chunk)
    except Exception as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(500, f"写入分块失败: {e} / Failed to write chunk: {e}")

    digest = md5.hexdigest()
    if expected_md5 and digest.lower() != expected_md5:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, f"分块 MD5 不匹配 / Chunk MD5 mismatch (expected={expected_md5}, actual={digest})")

    # 原子替换
    os.replace(tmp_path, chunk_path)
    return {"received": True, "index": index, "size": total, "md5": digest}


@router.get("/api/heapdump-reports/uploads/{uid}/status")
async def upload_status(request: Request, uid: str):
    """返回已接收的分块索引列表（供断点续传）。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    _check_session_owner(uid, user_id)

    d = _upload_dir(uid)
    parts = []
    for p in d.glob("*.part"):
        try:
            parts.append(int(p.stem))
        except ValueError:
            continue
    parts.sort()
    return {"upload_id": uid, "received_chunks": parts, "chunk_size": DEFAULT_CHUNK_SIZE}


@router.post("/api/heapdump-reports/uploads/{uid}/complete")
async def complete_upload(request: Request, uid: str):
    """合并分块 → 校验魔数 → 移入 NFS dump_dir → 建 QUEUED 记录。

    Body (JSON): {
        "session_id": "sess_xxx",   # 必需，报告归属会话
        "filename":   "app.hprof",  # 可选，前端原始文件名
        "parse_args": {},           # 可选，透传给 worker
    }
    """
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    _check_session_owner(uid, user_id)
    um = helpers._ensure_user_manager()

    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(400, "缺少 session_id / Missing session_id")
    helpers._check_session_owner(session_id, user_id)

    filename = _sanitize_filename(body.get("filename") or "heapdump.hprof")
    parse_args = body.get("parse_args") or {}
    if not isinstance(parse_args, dict):
        parse_args = {}
    # P1 (2026-07-09 code review): parse_args 白名单
    #
    # 之前客户端可传 ``xmx`` / ``mat_home`` 等键直通到 worker：
    #   - xmx 被 ``java -Xmx{xmx}`` 子进程参数消费，攻击者可设超大值
    #     撑爆 worker 主机 OOM
    #   - mat_home 被 ``os.path.join(mat_home, "plugins", ...)`` 消费，
    #     可触发任意路径 glob 读
    #
    # 只允许在 ``_PARSE_ARGS_ALLOWLIST`` 中的键；hprof_kind / hprof_file
    # 由服务端在 merge 完成后基于魔数设置，不来自客户端。
    _filtered: dict = {}
    for k, v in parse_args.items():
        if k in _PARSE_ARGS_ALLOWLIST:
            _filtered[k] = v
    dropped = sorted(set(parse_args.keys()) - set(_filtered.keys()))
    if dropped:
        _logger.warning(
            "complete upload: dropping non-allowlisted parse_args keys for user=%s dropped=%s",
            user_id, dropped,
        )
    parse_args = _filtered

    # 分块清单
    d = _upload_dir(uid)
    parts = sorted(
        (p for p in d.glob("*.part") if p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )
    if not parts:
        raise HTTPException(400, "没有已上传的分块 / No chunks uploaded")
    # 校验分块索引连续 (0..N-1)
    for i, p in enumerate(parts):
        if int(p.stem) != i:
            raise HTTPException(400, f"分块 {i} 缺失 / Chunk {i} missing")

    # 配额（file_upload）
    can, reason = um.try_consume_file_upload(user_id)
    if not can:
        raise HTTPException(429, reason)

    # 合并到 dump_dir。文件名按魔数决定扩展：MAT 靠 .hprof / .hprof.gz 区分是否解压。
    report_id = "hd_" + _uuid.uuid4().hex[:12]
    dump_dir = _dump_dir(report_id)
    dump_dir.mkdir(parents=True, exist_ok=True)
    tmp_hprof = dump_dir / ".app.merging"  # 先写临时名，确定 kind 后再 rename
    total_size = 0
    kind: Optional[str] = None
    try:
        with open(tmp_hprof, "wb") as out:
            for p in parts:
                with open(p, "rb") as src:
                    while True:
                        buf = src.read(1 << 20)  # 1 MiB
                        if not buf:
                            break
                        if kind is None:
                            kind = _detect_hprof_kind(buf[:32] if len(buf) >= 32 else buf)
                        out.write(buf)
                        total_size += len(buf)
            if kind is None:
                raise HTTPException(400, "合并结果为空 / Empty merge result")

        # 原子 rename 到最终文件名（MAT 依扩展名区分是否解压）
        final_name = "app.hprof.gz" if kind == "gzip" else "app.hprof"
        hprof_path = dump_dir / final_name
        os.replace(tmp_hprof, hprof_path)

        # 建 DB 记录（用与目录同名的 report_id）
        agent = helpers._get_agent(user_id)
        rid = agent.memory.add_heapdump_report(session_id, {
            "id": report_id,
            "filename": filename,
            "size": total_size,
            "dump_dir": str(dump_dir.resolve()),
            "parse_args": {**parse_args, "hprof_kind": kind, "hprof_file": final_name},
        })

    except HTTPException:
        # 回滚 NFS 目录（不回滚配额，已占存储）
        shutil.rmtree(dump_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(dump_dir, ignore_errors=True)
        _logger.error("complete upload failed: %s", e, exc_info=True)
        raise HTTPException(500, f"合并失败: {e} / Merge failed: {e}")

    # 清理上传临时目录（分块文件 + 元数据）
    shutil.rmtree(d, ignore_errors=True)

    log_audit(request, "report.heapdump.upload", user_id=user_id,
              resource=f"heapdump_report:{rid}",
              details={"session_id": session_id, "filename": filename, "size": total_size})

    return {
        "report_id": rid,
        "session_id": session_id,
        "filename": filename,
        "size": total_size,
        "status": "QUEUED",
        "dump_dir": str(dump_dir.resolve()),
        "hprof_kind": kind,          # 'plain' | 'gzip'
        "hprof_file": final_name,    # 'app.hprof' | 'app.hprof.gz'
    }


@router.delete("/api/heapdump-reports/uploads/{uid}")
async def cancel_upload(request: Request, uid: str):
    """用户主动放弃上传：清理临时目录。"""
    user_id = helpers._get_current_user(request)
    helpers._check_analysis_feature(user_id, "heapdump")
    _check_session_owner(uid, user_id)
    d = _upload_dir(uid)
    shutil.rmtree(d, ignore_errors=True)
    return {"cancelled": True}
