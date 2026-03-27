#!/usr/bin/python

# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging
import os
import subprocess
import sys
import urllib.parse

import git
import github3
import github3.exceptions as gh_exceptions
import re
import requests


class GitHubBranch:
    def __init__(self, ns, name, branch) -> None:
        self.ns = ns
        self.name = name
        self.branch = branch

    def __str__(self) -> str:
        return f"{self.ns}/{self.name}:{self.branch}"


class GitBranch:
    def __init__(self, url, branch) -> None:
        self.url = url
        self.branch = branch

    def __str__(self) -> str:
        return f"{self.url}:{self.branch}"


class RepoException(Exception):
    """An error requiring the user to perform a manual action in the
    destination repo
    """

    pass


class MakeException(Exception):
    """An non-fatal error when running make merge-bot, the PR will be sent but
    the user should be informed that the make failed, has to manually run
    the command and fix the issue."""

    pass


def check_conflict(repo):
    unmerged_blobs = repo.index.unmerged_blobs()

    for v in unmerged_blobs.values():
        for stage, blob in v:
            if stage != 0:
                return True

    return False


def configure_commit_info(repo, bot_name, bot_email):
    with repo.config_writer() as config:
        config.set_value("user", "email", bot_email)
        config.set_value("user", "name", bot_name)


def git_merge(gitwd, dest, source, merge):
    orig_commit = gitwd.active_branch.commit

    if merge.branch in gitwd.remotes.merge.refs:
        # Check if we have already pushed a merge to the merge branch which
        # contains the current head of the source branch
        try:
            gitwd.git.merge_base(
                f"source/{source.branch}", f"merge/{merge.branch}", is_ancestor=True
            )
            logging.info("Existing merge branch already contains source")

            # Let's double-check if it's not merged already to the dest. If so
            # we don't need to update the PR.
            dest_commit = gitwd.remotes.dest.refs[dest.branch].commit
            merge_commit = gitwd.remotes.merge.refs[merge.branch].commit
            if len(dest_commit.parents) > 1:  # Only check if it's merge commit
                for parent in dest_commit.parents:
                    if parent == merge_commit:  # We're up to date.
                        return False

            # We're not going to update merge branch, but we still want to
            # ensure there's a PR open on it.
            gitwd.head.reference = gitwd.remotes.merge.refs[merge.branch]
            gitwd.head.reset(index=True, working_tree=True)
            return True
        except git.exc.GitCommandError:
            # merge_base --is-ancestor indicates true/false by raising an
            # exception or not
            logging.info("Existing merge branch needs to be updated")

    logging.info("Performing merge")
    try:
        gitwd.git.merge(f"source/{source.branch}", "-X", "ours")
    except git.exc.GitCommandError as ex:
        raise RepoException(f"Git merge failed: {ex}")

    if gitwd.is_dirty():
        if check_conflict(gitwd):
            raise RepoException("Merge conflict, needs manual resolution!")

        logging.info("Committing merge")
        gitwd.index.commit(
            f"Merge {source.url}:{source.branch} into {dest.branch}",
            parent_commits=(
                orig_commit,
                gitwd.remotes.source.refs[source.branch].commit,
            ),
        )
        return True

    if gitwd.active_branch.commit != orig_commit:
        logging.info("Destination can be fast-forwarded")
        return True

    logging.info("No merge is necessary")
    return False


def message_slack(webhook_url, msg):
    if webhook_url is None:
        logging.info(msg)
        return

    requests.post(
        webhook_url,
        json={
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": msg},
                }
            ]
        },
    )


def error_slack(webhook_url, msg, err):
    if webhook_url is None:
        logging.info(msg)
        return

    requests.post(
        webhook_url,
        json={
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": msg},
                },
                {
                    "type": "divider",
                },
                # We assume there are no errant code fences here. If there are,
                # formatting will be broken.
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```{err}```"},
                },
            ]
        },
    )


def grab_go_version_from_go_mod():
    try:
        with open("go.mod", "r") as f:
            txt = f.read()
            match = re.search(r"go\s+(\d+.\d+)", txt)
            if match:
                return match.group(1)
    except Exception:
        logging.info("Failed to discover go version from go.mod")
    return ""


# Run `make merge-bot` to allow projects to run custom logic when merging
# upstream changes. This is useful for projects that need to update vendored
# dependencies or run other tasks after an upstream merge.
# This is a best effort, and if the target doesn't exist, we ignore the error.
def commit_run_make(repo):
    try:
        proc = subprocess.run(
            "make merge-bot", shell=True, check=True, capture_output=True
        )
        logging.debug(f"make merge-bot output: {proc.stdout.decode()}")
    except subprocess.CalledProcessError as err:
        if "No rule to make target" not in err.stderr.decode():
            raise MakeException(
                f"Error running `make merge-bot`. Manual intervention is needed: {err}: {err.stderr.decode()}"
            )
        else:
            logging.info("No make target for merge-bot, skipping")

    if repo.is_dirty():
        commit(repo, "CARRY: running make merge-bot")

    return


def commit(repo, msg):
    try:
        repo.git.add(all=True)
        repo.git.commit("-m", msg)
    except Exception as err:
        err.extra_info = "Unable to commit changes in git"
        raise err

    return


def commit_go_mod_updates(repo):
    try:
        go_version = grab_go_version_from_go_mod()
        if len(go_version) > 0:
            compat = f"-compat={go_version}"
        else:
            compat = ""
        proc = subprocess.run(
            f"go mod tidy {compat}", shell=True, check=True, capture_output=True
        )
        logging.debug(f"go mod tidy output: {proc.stdout.decode()}")
        proc = subprocess.run(
            "go mod vendor", shell=True, check=True, capture_output=True
        )
        logging.debug(f"go mod vendor output: {proc.stdout.decode()}")
    except subprocess.CalledProcessError as err:
        raise RepoException(
            f"Unable to update go modules: {err}: {err.stderr.decode()}"
        )

    if repo.is_dirty():
        commit(repo, "Updating and vendoring go modules after an upstream merge")

    return


def push(gitwd, merge):
    result = gitwd.remotes.merge.push(refspec=f"HEAD:{merge.branch}", force=True)
    if result[0].flags & git.PushInfo.ERROR != 0:
        raise Exception(f"Error pushing to {merge}: {result[0].summary}")

    # Return True if changes were pushed, False if no changes were pushed.
    # Note: PushInfo.UP_TO_DATE indicates that nothing changed
    return not bool(result[0].flags & git.PushInfo.UP_TO_DATE)


def create_pr(g, dest_repo, dest, source, merge):
    logging.info("Checking for existing pull request")
    try:
        pr = dest_repo.pull_requests(head=f"{merge.ns}:{merge.branch}").next()
        return pr.html_url, False
    except StopIteration:
        pass

    logging.info("Creating a pull request")
    # XXX(mdbooth): This hack is because github3 doesn't support setting
    # maintainer_can_modify to false when creating a PR.
    #
    # When maintainer_can_modify is true, which is the default we can't change,
    # we get a 422 response from GitHub. The reason for this is that we're
    # creating the pull in the destination repo with credentials that don't
    # have write permission on the source. This means they can't grant
    # permission to the maintainer at the destination to modify the merge
    # branch.
    #
    # https://github.com/sigmavirus24/github3.py/issues/1031

    pr = g._post(
        f"https://api.github.com/repos/{dest.ns}/{dest.name}/pulls",
        data={
            "title": f"Merge {source.url}:{source.branch} into {dest.branch}",
            "head": f"{merge.ns}:{merge.branch}",
            "base": dest.branch,
            "maintainer_can_modify": False,
        },
        json=True,
    )
    pr.raise_for_status()

    return pr.json()["html_url"], True


def github_token_login(token):
    logging.info("Logging to GitHub with personal access token")
    return github3.login(token=token)


def github_app_login(gh_app_id, gh_app_key):
    logging.info("Logging to GitHub")
    g = github3.GitHub()
    g.login_as_app(gh_app_key, gh_app_id, expire_in=300)
    return g


def github_login_for_repo(g, gh_account, gh_repo_name, gh_app_id, gh_app_key):
    try:
        install = g.app_installation_for_repository(
            owner=gh_account, repository=gh_repo_name
        )
    except gh_exceptions.NotFoundError:
        msg = (
            f"App has not been authorised by {gh_account}, or repo "
            f"{gh_account}/{gh_repo_name} does not exist"
        )
        logging.error(msg)
        raise Exception(msg)

    g.login_as_app_installation(gh_app_key, gh_app_id, install.id)
    return g


def init_working_dir(
    source_url,
    source_branch,
    dest_url,
    dest_branch,
    merge_url,
    merge_branch,
    gh_app,  # Read permission on dest
    gh_cloner_app,  # Write permission on merge
    bot_email,
    bot_name,
):
    gitwd = git.Repo.init(path=".")

    for remote, url in [
        ("source", source_url),
        ("dest", dest_url),
        ("merge", merge_url),
    ]:
        if remote in gitwd.remotes:
            gitwd.remotes[remote].set_url(url)
        else:
            gitwd.create_remote(remote, url)

    # We want to avoid writing app credentials to disk. We write them to files
    # in /dev/shm/credentials and configure git to read them from there as
    # required.
    # This isn't perfect because /dev/shm can still be swapped, but this whole
    # executable can be swapped, so it's no worse than that.
    credentials_dir = "/dev/shm/credentials"
    app_credentials = os.path.join(credentials_dir, "app")
    cloner_credentials = os.path.join(credentials_dir, "cloner")

    if not os.path.exists(credentials_dir):
        os.mkdir(credentials_dir)
    with open(app_credentials, "w") as f:
        f.write(gh_app.session.auth.token)
    with open(cloner_credentials, "w") as f:
        f.write(gh_cloner_app.session.auth.token)

    with gitwd.config_writer() as config:
        config.set_value("credential", "username", "x-access-token")
        config.set_value("credential", "useHttpPath", "true")

        for repo, credentials in [
            (dest_url, app_credentials),
            (merge_url, cloner_credentials),
        ]:
            config.set_value(
                f'credential "{repo}"',
                "helper",
                f'"!f() {{ echo "password=$(cat {credentials})"; }}; f"',
            )

        config.set_value("user", "email", bot_email)
        config.set_value("user", "name", bot_name)

    logging.info(f"Fetching {dest_branch} from dest")
    gitwd.remotes.dest.fetch(dest_branch)
    logging.info(f"Fetching {source_branch} from source")
    gitwd.remotes.source.fetch(source_branch)

    working_branch = f"dest/{dest_branch}"
    logging.info(f"Checking out {working_branch}")

    logging.info(f"Checking for existing merge branch {merge_branch} in {merge_url}")
    merge_ref = gitwd.git.ls_remote("merge", merge_branch, heads=True)
    if len(merge_ref) > 0:
        logging.info("Fetching existing merge branch")
        gitwd.remotes.merge.fetch(merge_branch)

    head_commit = gitwd.remotes.dest.refs[dest_branch].commit
    if "merge" in gitwd.heads:
        gitwd.heads.merge.set_commit(head_commit)
    else:
        gitwd.create_head("merge", head_commit)
    gitwd.head.reference = gitwd.heads.merge
    gitwd.head.reset(index=True, working_tree=True)

    return gitwd


def run(
    source,
    dest,
    merge,
    working_dir,
    bot_name,
    bot_email,
    gh_app_id,
    gh_app_key,
    gh_cloner_id,
    gh_cloner_key,
    slack_webhook,
    update_go_modules=False,
    run_make=False,
    gh_token=None,
):
    logging.basicConfig(
        format="%(levelname)s - %(message)s", stream=sys.stdout, level=logging.INFO
    )

    if gh_token:
        gh_app = github_token_login(gh_token)
        gh_cloner_app = github_token_login(gh_token)
    else:
        # App credentials for accessing the destination and opening a PR
        gh_app = github_app_login(gh_app_id, gh_app_key)
        gh_app = github_login_for_repo(
            gh_app, dest.ns, dest.name, gh_app_id, gh_app_key
        )

        # App credentials for writing to the merge repo
        gh_cloner_app = github_app_login(gh_cloner_id, gh_cloner_key)
        gh_cloner_app = github_login_for_repo(
            gh_cloner_app, merge.ns, merge.name, gh_cloner_id, gh_cloner_key
        )

    try:
        dest_repo = gh_app.repository(dest.ns, dest.name)
        logging.info(f"Destination repository is {dest_repo.clone_url}")
        merge_repo = gh_cloner_app.repository(merge.ns, merge.name)
        logging.info(f"Merge repository is {merge_repo.clone_url}")
    except Exception as ex:
        logging.exception(ex)
        error_slack(
            slack_webhook,
            "An error occurred fetching repo information from GitHub",
            ex,
        )
        return False

    try:
        os.mkdir(working_dir)
    except FileExistsError:
        pass
    except Exception:
        raise

    try:
        os.chdir(working_dir)
        gitwd = init_working_dir(
            source.url,
            source.branch,
            dest_repo.clone_url,
            dest.branch,
            merge_repo.clone_url,
            merge.branch,
            gh_app,
            gh_cloner_app,
            bot_email,
            bot_name,
        )
    except Exception as ex:
        logging.exception(ex)
        error_slack(
            slack_webhook,
            "An error occurred initialising the git directory",
            ex,
        )
        return False

    try:
        if not git_merge(gitwd, dest, source, merge):
            return True

        if update_go_modules:
            commit_go_mod_updates(gitwd)

        if run_make:
            commit_run_make(gitwd)
    except MakeException as ex:
        # We don't want to fail the merge if make fails, but we should inform
        # the user that they need to run make merge-bot manually and fix the
        # issue.
        logging.warning(ex)
        pass
    except RepoException as ex:
        logging.error(ex)
        try:
            source = urllib.parse.urlparse(source).path.lstrip("/")
        except Exception:
            pass
        error_slack(
            slack_webhook,
            f"Manual intervention is needed to merge {source} into {dest}",
            ex,
        )
        return True
    except Exception as ex:
        logging.exception(ex)
        try:
            source = urllib.parse.urlparse(source).path.lstrip("/")
        except Exception:
            pass
        error_slack(
            slack_webhook,
            f"An error occurred trying to merge {source} into {dest}",
            ex,
        )
        return False

    try:
        push_result = push(gitwd, merge)
    except Exception as ex:
        logging.exception(ex)
        error_slack(
            slack_webhook,
            f"An error occurred pushing to {merge}",
            ex,
        )
        return False

    try:
        pr_url, created = create_pr(gh_app, dest_repo, dest, source, merge)
        logging.info(f"Merge PR is {pr_url}")
    except Exception as ex:
        logging.exception(ex)
        error_slack(slack_webhook, "An error occurred creating a merge PR", ex)
        return False

    if created:
        message_slack(slack_webhook, f"A new merge PR was created: <{pr_url}>")
    else:
        if push_result:
            message_slack(
                slack_webhook, f"An existing merge PR was updated: <{pr_url}>"
            )
        else:
            logging.info(f"No changes pushed to existing PR: <{pr_url}>")

    return True
