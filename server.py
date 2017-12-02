#!/usr/bin/env python3
import sys
import time
from wsgiref.simple_server import make_server, WSGIRequestHandler

import arrow as arrow
from logbook import Logger, StreamHandler
from logbook.compat import redirect_logging
from pyramid.config import Configurator
from pyramid.request import Request
from pyramid.response import Response
from pyramid.view import view_config
from slacker import Slacker

# Enable logging
redirect_logging()
StreamHandler(sys.stderr).push_application()
logger = Logger("slack-repo-notif")

# Setup the slack handler
try:
    slack = Slacker(sys.argv[1])
except IndexError:
    print("Must pass your slack bot token on the command line.", file=sys.stdout)

try:
    report_channel = sys.argv[2]
except IndexError:
    report_channel = "repo-report"
    logger.warning("Using default report channel repo-report")


@view_config(
    route_name='webhook',
    request_method='POST'
)
def webhook(request: Request) -> Response:
    """
    Main webhook URL handler.
    """
    hook: str = request.headers.get("X-Gitlab-Event")
    if hook is None:
        return Response("Invalid event", 400)

    if not hook.endswith("Hook"):
        return Response("Invalid event", 400)

    # extract the actual event, since gitlab sends weird events
    event = hook.split("Hook")[0].lower().lstrip().rstrip()
    try:
        event_handler = globals()[f"handle_{event.replace(' ', '_')}"]
    except KeyError:
        logger.info(f"Got unknown event {event}")
        return Response(f"Invalid event '{event}'", 400)

    # call the event handler
    event_handler(request)
    return Response("", 200)


# Event handlers
def handle_push(request: Request):
    """
    Handles a push event.
    """
    body = request.json

    # get the title
    username = body["user_username"]
    commit_count = body["total_commits_count"]
    ref = body["ref"]
    repo_name = body["project"]["path_with_namespace"]

    title = f"{username} pushed {commit_count} commit(s) to {ref} @ {repo_name}"
    title_link = body["project"]["web_url"]

    # get the description
    lines = []

    added = set()
    changed = set()
    removed = set()

    for commit in body["commits"]:
        message = commit['message'].split("\n")[0]
        fmtted = f"`<{commit['url']}|{commit['id'][0:6]}>` {message}"
        lines.append(fmtted)

        added.update(set(commit["added"]))
        changed.update(set(commit["modified"]))
        removed.update(set(commit["removed"]))

    lines.append("\nChanges:")
    lines.append("\n".join("`A: {}`".format(x) for x in added))
    lines.append("\n".join("`E: {}`".format(x) for x in changed))
    lines.append("\n".join("`R: {}`".format(x) for x in removed))

    text = '\n'.join(lines)

    # build the attachment
    attachment = {
        "title": title,
        "title_link": title_link,
        "text": text,
        "ts": time.time(),

        "author_name": body["user_username"],
        "author_icon": body["user_avatar"],

        # needed to parse markdown in the text
        "mrkdwn_in": ["text"],
        "color": "#00ff00"
    }
    slack.chat.post_message(report_channel,
                            as_user=True,
                            attachments=[attachment])


def handle_pipeline(request: Request):
    """
    Handles a pipeline webhook.
    """
    body = request.json

    pipeline_data = body["object_attributes"]
    if pipeline_data["status"] == "pending":
        _handle_pending_pipeline(body)
        return

    if pipeline_data["status"] == "running":
        _handle_running_pipeline(body)
        return

    if pipeline_data["status"] == "success":
        _handle_successful_pipeline(body)
        return

    if pipeline_data["status"] == "failed":
        _handle_failed_pipeline(body)


def _handle_pending_pipeline(body: dict):
    """
    Handles a pending pipeline.
    """
    repo_name = body["project"]["path_with_namespace"]
    pipeline_properites = body["object_attributes"]

    # build the URL (project URL + pipelines + id)
    title_link = body["project"]["web_url"] + f"/pipelines/{pipeline_properites['id']}"
    title = f"Pipeline for {repo_name}"

    # build the body
    lines = [f"This pipeline has *{len(body['builds'])}* build(s):"]
    for build in body["builds"]:
        lines.append(f" - Build `{build['name']}`: `{build['status']}`")

    text = '\n'.join(lines)

    attachment = {
        "title": title,
        "title_link": title_link,
        "text": text,

        "ts": arrow.get(pipeline_properites["created_at"]).datetime.timestamp(),

        "mrkdwn_in": ["text"],

        "author_name": body["user"]["username"],
        "author_icon": body["user"]["avatar_url"],

        "color": "#1976d2"
    }
    slack.chat.post_message(report_channel,
                            as_user=True,
                            attachments=[attachment])


def _handle_running_pipeline(body: dict):
    """
    Handles a running pipeline.
    """
    repo_name = body["project"]["path_with_namespace"]
    pipeline_properites = body["object_attributes"]

    # build the URL (project URL + pipelines + id)
    title_link = body["project"]["web_url"] + f"/pipelines/{pipeline_properites['id']}"
    title = f"Pipeline for {repo_name}"

    lines = ["Pipeline update: "]
    for build in body["builds"]:
        lines.append(f"- Build `{build['name']}`: Stage `{build['stage']}` "
                     f"/ Status `{build['status']}`")

    text = "\n".join(lines)

    attachment = {
        "title": title,
        "title_link": title_link,
        "text": text,

        "ts": arrow.get(pipeline_properites["created_at"]).datetime.timestamp(),

        "mrkdwn_in": ["text"],

        "author_name": body["user"]["username"],
        "author_icon": body["user"]["avatar_url"],

        "color": "#3f51b5"
    }
    slack.chat.post_message(report_channel,
                            as_user=True,
                            attachments=[attachment])


def _handle_successful_pipeline(body: dict):
    """
    Handle a successful pipeline.
    """
    repo_name = body["project"]["path_with_namespace"]
    pipeline_properites = body["object_attributes"]

    # build the URL (project URL + pipelines + id)
    title_link = body["project"]["web_url"] + f"/pipelines/{pipeline_properites['id']}"
    print(title_link)
    title = f"Pipeline for {repo_name}"

    lines = ["Pipeline update: "]
    fields = []
    for build in body["builds"]:
        lines.append(f" - Build `{build['name']}`: Stage `{build['stage']}` "
                     f"/ Status `{build['status']}`")
        if build["artifacts_file"]["filename"] is None:
            continue

        artifact_url = f"{body['project']['web_url']}/-/jobs/{build['id']}/artifacts/download"
        artifact_size = build["artifacts_file"]["size"] / 1024 / 1024

        fields.append({
            "title": f"Artifact for {build['name']}",
            "value": f"<{artifact_url}|Download ({artifact_size:.2f} MiB)>",
            "short": False
        })

    text = '\n'.join(lines)

    attachment = {
        "title": title,
        "title_link": title_link,
        "text": text,
        "fields": fields,

        "ts": arrow.get(pipeline_properites["finished_at"]).datetime.timestamp(),

        "mrkdwn_in": ["text"],

        "author_name": body["user"]["username"],
        "author_icon": body["user"]["avatar_url"],

        "color": "#00e676"
    }
    slack.chat.post_message(report_channel,
                            as_user=True,
                            attachments=[attachment])


def _handle_failed_pipeline(body: dict):
    """
    Handles a failed pipeline.
    """
    repo_name = body["project"]["path_with_namespace"]
    pipeline_properites = body["object_attributes"]

    # build the URL (project URL + pipelines + id)
    title_link = body["project"]["web_url"] + f"/pipelines/{pipeline_properites['id']}"
    title = f"Pipeline for {repo_name}"

    lines = ["Pipeline failed: "]
    for build in body["builds"]:
        lines.append(f"- Build `{build['name']}`: Stage `{build['stage']}` "
                     f"/ Status `{build['status']}`")

    text = "\n".join(lines)

    attachment = {
        "title": title,
        "title_link": title_link,
        "text": text,

        "ts": arrow.get(pipeline_properites["finished_at"]).datetime.timestamp(),

        "mrkdwn_in": ["text"],

        "author_name": body["user"]["username"],
        "author_icon": body["user"]["avatar_url"],

        "color": "#ff0000"
    }
    slack.chat.post_message(report_channel,
                            as_user=True,
                            attachments=[attachment])


def handle_tag_push(request: Request):
    """
    Handles a tag push.
    """
    body = request.json
    repo_name = body["project"]["path_with_namespace"]

    # check if it's a branch (head) or a tag
    ref = body['ref']
    if ref.split("/")[1] == "heads":
        word = "branch"
    else:
        word = "tag"

    title = f"{body['user_name']} created {word} {body['ref'].split('/')[-1]} on {repo_name}"
    title_link = body["project"]["web_url"]

    # build the sha URL
    sha_url = title_link + f"/commit/{body['checkout_sha']}"
    text = f"Points to `<{sha_url}|{body['checkout_sha']}>`"

    attachment = {
        "title": title,
        "title_link": title_link,
        "text": text,

        "ts": time.time(),

        "mrkdwn_in": ["text"],

        "author_name": body["user_name"],
        "author_icon": body["user_avatar"],

        "color": "#26a69a"
    }

    slack.chat.post_message(report_channel, as_user=True, attachments=[attachment])


if __name__ == '__main__':
    with Configurator() as config:
        config.add_route('webhook', '/webhook')
        config.scan()
        app = config.make_wsgi_app()


    class LoggingWSGIRequestHandler(WSGIRequestHandler):
        def log_message(self, format, *args):
            message = format % args
            logger.info(f"{self.address_string()} - {message}")


    server = make_server('0.0.0.0', 6543, app, handler_class=LoggingWSGIRequestHandler)
    logger.info("Serving forever on 0.0.0.0:6543")
    server.serve_forever()
