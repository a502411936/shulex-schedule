#!/usr/bin/env python3
"""
eBay 发送失败工单自动打标签脚本
功能：筛选当日 eBay 渠道中 sendFailureTime != null 的工单，自动绑定 "Sending failed" 标签
用法：python3 ebay_sending_failed_tagger.py
定时：建议每天执行一次，例如 cron: 0 2 * * * python3 /path/to/ebay_sending_failed_tagger.py
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────
# 配置（通过环境变量注入，不要硬编码）
# ─────────────────────────────────────────────
BASE_URL    = os.environ.get("SOLVEA_BASE_URL", "https://desk.shulex.com")
TOKEN       = os.environ.get("SOLVEA_TOKEN")          # 必填：Bearer token
PROJECT_ID  = os.environ.get("SOLVEA_PROJECT_ID")     # 必填：项目 ID

TAG_ID      = 184567          # ebay > Sending failed 标签节点 ID
TAG_NAME    = "Sending failed"
PAGE_SIZE   = 200
RETRY_TIMES = 3               # 单次请求最大重试次数
RETRY_DELAY = 2               # 重试间隔秒数

# ─────────────────────────────────────────────
# 日志
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


def check_config():
    if not TOKEN:
        log.error("缺少环境变量 SOLVEA_TOKEN")
        sys.exit(1)
    if not PROJECT_ID:
        log.error("缺少环境变量 SOLVEA_PROJECT_ID")
        sys.exit(1)


def get_headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Referer": "/botdesk",
    }


def request_with_retry(method, url, **kwargs):
    """带重试的 HTTP 请求"""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            resp = requests.request(method, url, headers=get_headers(), timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"请求失败（第 {attempt} 次）: {e}")
            if attempt < RETRY_TIMES:
                time.sleep(RETRY_DELAY)
    log.error(f"请求连续失败 {RETRY_TIMES} 次，放弃: {url}")
    return None


def list_tickets_page(page: int):
    url = f"{BASE_URL}/api_v2/tars/api/ticket/list"
    return request_with_retry("POST", url, json={
        "page": page,
        "pageSize": PAGE_SIZE,
        "viewId": "1",
    })


def bind_tag(ticket_number: str):
    url = f"{BASE_URL}/api_v2/tars/api/tags/manual/binding"
    result = request_with_retry("POST", url, json={
        "ticketNum": ticket_number,
        "tags": [{"id": TAG_ID, "name": TAG_NAME}],
    })
    return result and result.get("success")


def main():
    check_config()

    # 今日 UTC 起始时间（当天 00:00:00 UTC）
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    # 往前多取一天做缓冲，避免时区边界漏单
    since = (today_start - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    log.info(f"开始执行：筛选 {since} 之后的 eBay 发送失败工单")

    found = []
    page = 1
    total = None
    scanned = 0
    stop = False

    while not stop:
        data = list_tickets_page(page)
        if not data or not data.get("success"):
            log.error(f"第 {page} 页请求失败: {data}")
            break

        block = data.get("data") or {}
        tickets = block.get("list", [])

        if total is None:
            total = block.get("total", 0)
            log.info(f"总工单数: {total}")

        if not tickets:
            break

        for t in tickets:
            update_time = t.get("lastUpdateTime", "")
            if update_time and update_time < since:
                stop = True
                break

            scanned += 1
            if t.get("channel") == "ebay" and t.get("sendFailureTime") is not None:
                found.append({
                    "ticketNumber": t.get("ticketNumber"),
                    "sendFailureTime": t.get("sendFailureTime"),
                    "subject": (t.get("subject") or "")[:80],
                })

        if len(tickets) < PAGE_SIZE:
            break

        page += 1

    log.info(f"扫描完成：共扫描 {scanned} 条，找到 {len(found)} 条目标工单")

    if not found:
        log.info("无需打标签，退出。")
        return

    success_count = 0
    fail_list = []

    for t in found:
        ok = bind_tag(t["ticketNumber"])
        if ok:
            success_count += 1
            log.info(f"  ✓ #{t['ticketNumber']} | {t['sendFailureTime']} | {t['subject']}")
        else:
            fail_list.append(t["ticketNumber"])
            log.warning(f"  ✗ #{t['ticketNumber']} 绑定失败")
        time.sleep(0.1)

    log.info(f"完成：成功 {success_count}/{len(found)} 张")
    if fail_list:
        log.warning(f"失败工单号: {fail_list}")
        sys.exit(1)  # 有失败时以非零退出码退出，便于 cron 告警


if __name__ == "__main__":
    main()
