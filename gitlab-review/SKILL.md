---
name: "gitlab-review"
description: "Receives GitLab webhook events, performs AI code review on MR/Push changes, and posts results to GitLab. Invoke when GitLab webhook is triggered or user asks for GitLab code review integration."
---

# GitLab Code Review Skill

This skill receives GitLab webhook events (Merge Request and Push), performs AI-powered code review on the changes, and posts review comments back to GitLab MR/Commit. It can also send review results to a DingTalk channel.

## Usage

This skill is triggered via HTTP webhook from GitLab. No manual invocation required.

## Configuration

The following environment variables need to be configured:

- `GITLAB_SECRET_TOKEN`: Secret token for validating GitLab webhook requests
- `GITLAB_API_TOKEN`: GitLab Personal Access Token with api scope for adding comments
- `DINGTALK_WEBHOOK_URL`: (Optional) DingTalk robot webhook URL for sending notifications

## Webhook Endpoint

- Route: `/gitlab-webhook`
- Method: POST
- Required Headers:
  - `X-Gitlab-Token`: GitLab secret token for validation
  - `X-Gitlab-Event`: Event type (Merge Request Hook, Push Hook)

## Supported Events

### Merge Request Hook
When a new MR is created or updated, the skill will:
1. Fetch the MR changes from GitLab API
2. Send the diff to AI for code review
3. Post the review comments to the MR
4. Optionally send notifications to DingTalk

### Push Hook
When code is pushed to a branch, the skill will:
1. Fetch the commit changes
2. Send the diff to AI for code review
3. Post the review comments to the commit
4. Optionally send notifications to DingTalk

## Implementation

The skill implementation should be in `skill.py` in the same directory. Key functions:

- `handle_gitlab_webhook(request)`: Main webhook handler
- `process_merge_request(payload)`: Process MR events
- `process_push(payload)`: Process Push events  
- `ai_code_review(changes)`: Call AI model for code review
- `post_comment_to_gitlab(project_id, mr_iid, comment)`: Post comment to GitLab MR
- `send_dingtalk_notification(message)`: Send notification to DingTalk
