import os
import json
import hmac
import hashlib
import logging
from typing import Dict, Any, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GITLAB_SECRET_TOKEN = os.getenv("GITLAB_SECRET_TOKEN", "")
GITLAB_API_TOKEN = os.getenv("GITLAB_API_TOKEN", "")
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL", "")
GITLAB_API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_API_URL = os.getenv("MINIMAX_API_URL", "https://api.minimaxi.com/v1")

# Create FastAPI app instance
app = FastAPI()


def validate_gitlab_token(token: str) -> bool:
    if not GITLAB_SECRET_TOKEN:
        return True
    return token == GITLAB_SECRET_TOKEN


@app.post("/gitlab-webhook")
async def handle_gitlab_webhook(request: Request) -> Dict[str, Any]:
    # Get headers and body
    headers = dict(request.headers)
    body = await request.json()

    # Debug: print all headers
    logger.info(f"Received headers: {headers}")

    token = headers.get("x-gitlab-token", "")
    if not validate_gitlab_token(token):
        return {"status": "error", "message": "Invalid token", "code": 403}

    event_type = headers.get("x-gitlab-event", "")
    logger.info(f"Received GitLab event: {event_type}")

    try:
        if event_type == "Merge Request Hook":
            result = await process_merge_request(body)
        elif event_type == "Push Hook":
            result = await process_push(body)
        else:
            logger.info(f"Unhandled event type: {event_type}")
            result = {"status": "ok", "message": f"Unhandled event type: {event_type}"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        result = {"status": "error", "message": str(e)}

    return result


async def process_merge_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    mr = payload.get("object_attributes", {})
    project = payload.get("project", {})

    if not mr or not project:
        return {"status": "error", "message": "Invalid payload"}

    mr_iid = mr.get("iid")
    project_id = project.get("id")
    project_web_url = project.get("web_url", "")

    logger.info(f"Processing MR !{mr_iid} for project {project.get('name')}")

    changes = await get_mr_changes(project_id, mr_iid)
    if not changes:
        return {"status": "error", "message": "Failed to get MR changes"}

    review_comments = await ai_code_review(changes)

    if review_comments:
        comment_result = await post_comment_to_gitlab(
            project_id, mr_iid, 
            f"## AI Code Review\n\n{review_comments}"
        )
        logger.info(f"Posted review comment to MR: {comment_result}")

        if DINGTALK_WEBHOOK_URL:
            dingtalk_result = await send_dingtalk_notification(
                f"MR !{mr_iid} 代码评审完成\n\n{review_comments}"
            )
            logger.info(f"Sent DingTalk notification: {dingtalk_result}")

    return {"status": "ok", "message": "MR processed successfully"}


async def process_push(payload: Dict[str, Any]) -> Dict[str, Any]:
    project = payload.get("project", {})
    commits = payload.get("commits", [])

    if not project or not commits:
        return {"status": "error", "message": "Invalid payload"}

    project_id = project.get("id")
    project_name = project.get("name")

    logger.info(f"Processing push for project {project_name} with {len(commits)} commits")

    for commit in commits:
        commit_id = commit.get("id", "")[:8]
        diff_content = await get_commit_diff(project_id, commit.get("id", ""))

        if diff_content:
            review_comments = await ai_code_review_for_commit(diff_content, commit_id)

            if review_comments:
                await post_commit_comment(project_id, commit_id, review_comments)

                if DINGTALK_WEBHOOK_URL:
                    await send_dingtalk_notification(
                        f"Commit {commit_id} 代码评审完成\n\n{review_comments}"
                    )

    return {"status": "ok", "message": "Push processed successfully"}


async def get_mr_changes(project_id: int, mr_iid: int) -> Optional[Dict[str, Any]]:
    if not GITLAB_API_TOKEN:
        logger.warning("GITLAB_API_TOKEN not set")
        return None

    url = f"{GITLAB_API_URL}/projects/{project_id}/merge_requests/{mr_iid}/changes"
    headers = {"PRIVATE-TOKEN": GITLAB_API_TOKEN}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get MR changes: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error getting MR changes: {e}")
            return None


async def get_commit_diff(project_id: int, commit_id: str) -> Optional[str]:
    if not GITLAB_API_TOKEN:
        return None

    url = f"{GITLAB_API_URL}/projects/{project_id}/repository/commits/{commit_id}/diff"
    headers = {"PRIVATE-TOKEN": GITLAB_API_TOKEN}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            if response.status_code == 200:
                diffs = response.json()
                return "\n".join([
                    f"File: {d.get('new_path', '')}\n{d.get('diff', '')}"
                    for d in diffs
                ])
            return None
        except Exception as e:
            logger.error(f"Error getting commit diff: {e}")
            return None


async def ai_code_review(changes: Dict[str, Any]) -> str:
    review_content = []
    changes_list = changes.get("changes", [])

    for change in changes_list:
        file_path = change.get("new_path", "")
        diff = change.get("diff", "")

        prompt = f"""请对以下代码变更进行评审，指出潜在问题、代码风格问题和优化建议：

文件: {file_path}
变更内容:
{diff}

请用中文回复，结构如下：
## 总体评价
[简要评价]

## 问题列表
1. [问题描述]
2. [问题描述]

## 优化建议
[优化建议]"""

        ai_response = await call_ai_model(prompt)
        if ai_response:
            review_content.append(f"### 文件: {file_path}\n\n{ai_response}\n")

    return "\n---\n\n".join(review_content) if review_content else "代码评审未发现问题"


async def ai_code_review_for_commit(diff_content: str, commit_id: str) -> str:
    prompt = f"""请对以下提交 {commit_id} 的代码变更进行评审，指出潜在问题、代码风格问题和优化建议：

{diff_content}

请用中文回复，结构如下：
## 总体评价
[简要评价]

## 问题列表
1. [问题描述]
2. [问题描述]

## 优化建议
[优化建议]"""

    return await call_ai_model(prompt)


async def call_ai_model(prompt: str) -> str:
    if not MINIMAX_API_KEY:
        logger.error("MINIMAX_API_KEY not configured")
        return "AI 模型调用未配置。请配置 MINIMAX_API_KEY。"

    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "abab6.5s-chat",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MINIMAX_API_URL}/text/chatcompletion_v2",
                headers=headers,
                json=payload,
                timeout=60.0
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("choices", [{}])[0].get("message", {}).get("content", "无法获取响应内容")
            else:
                logger.error(f"MiniMax API error: {response.status_code} - {response.text}")
                return f"AI 模型调用失败: HTTP {response.status_code}"
    except Exception as e:
        logger.error(f"Error calling MiniMax API: {e}")
        return f"AI 模型调用错误: {str(e)}"


async def post_comment_to_gitlab(project_id: int, mr_iid: int, comment: str) -> Dict[str, Any]:
    if not GITLAB_API_TOKEN:
        return {"status": "error", "message": "GITLAB_API_TOKEN not set"}

    url = f"{GITLAB_API_URL}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    headers = {"PRIVATE-TOKEN": GITLAB_API_TOKEN}
    data = {"body": comment}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.post(url, headers=headers, json=data, timeout=10.0)
            if response.status_code in (200, 201):
                return {"status": "ok", "data": response.json()}
            else:
                logger.error(f"Failed to post comment: {response.status_code}")
                return {"status": "error", "message": f"HTTP {response.status_code}"}
        except Exception as e:
            logger.error(f"Error posting comment: {e}")
            return {"status": "error", "message": str(e)}


async def post_commit_comment(project_id: int, commit_id: str, comment: str) -> Dict[str, Any]:
    if not GITLAB_API_TOKEN:
        return {"status": "error", "message": "GITLAB_API_TOKEN not set"}

    url = f"{GITLAB_API_URL}/projects/{project_id}/repository/commits/{commit_id}/comments"
    headers = {"PRIVATE-TOKEN": GITLAB_API_TOKEN}
    data = {"note": comment}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.post(url, headers=headers, json=data, timeout=10.0)
            if response.status_code in (200, 201):
                return {"status": "ok", "data": response.json()}
            else:
                logger.error(f"Failed to post commit comment: {response.status_code}")
                return {"status": "error", "message": f"HTTP {response.status_code}"}
        except Exception as e:
            logger.error(f"Error posting commit comment: {e}")
            return {"status": "error", "message": str(e)}


async def send_dingtalk_notification(message: str) -> Dict[str, Any]:
    if not DINGTALK_WEBHOOK_URL:
        return {"status": "error", "message": "DINGTALK_WEBHOOK_URL not set"}

    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": "代码评审通知",
            "text": message
        }
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(DINGTALK_WEBHOOK_URL, json=data, timeout=10.0)
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    return {"status": "ok"}
            return {"status": "error", "message": "Failed to send DingTalk notification"}
        except Exception as e:
            logger.error(f"Error sending DingTalk notification: {e}")
            return {"status": "error", "message": str(e)}


# For CoPaw platform integration
# The app instance is exported for CoPaw to use
# CoPaw will handle the server startup and port management

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting GitLab webhook server...")
    logger.info("Webhook endpoint: http://localhost:7777/gitlab-webhook")
    uvicorn.run(app, host="0.0.0.0", port=7777)
