# merge-bot

`merge-bot` is a Python tool designed to simplify merging changes from an upstream Git repository into a downstream repository. It automates the process of fetching, merging, and committing changes with a variety of configurable options, making it a valuable tool for maintaining synchronized codebases.

## Features

- Merge changes from an upstream repository into a downstream GitHub branch.
- Commit changes using a custom bot name and email.
- Update Go modules and vendor dependencies in separate commits.
- Optionally run `make` commands and include changes in the merge process.
- Slack integration for notifications.

## Requirements

- Python 3.8 or higher
- Access to GitHub App credentials or a Personal Access Token
- Optional: Slack webhook credentials for notifications

## Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/shiftstack/merge-bot.git
cd merge-bot
pip install -r requirements.txt
```

## Usage

`merge-bot` provides several CLI options to customize its behavior. Below is the general usage syntax:

Using GitHub App credentials:
```bash
python merge-bot.py --source <source_repo:branch> --dest <dest_repo:branch> --merge <merge_branch> \
    --bot-name <bot_name> --bot-email <bot_email> --github-app-key <app_key_path> \
    --github-cloner-key <cloner_key_path> [options]
```

Using a Personal Access Token:
```bash
python merge-bot.py --source <source_repo:branch> --dest <dest_repo:branch> --merge <merge_branch> \
    --bot-name <bot_name> --bot-email <bot_email> --github-token <token_path> [options]
```

### CLI Options

| Option                 | Short | Required | Description                                                                                                                                               |
|------------------------|-------|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `--source`             | `-s`  | Yes      | The upstream repository in the format `<git url>:<branch>`. This doesn't need to be a GitHub URL.                                                         |
| `--dest`               | `-d`  | Yes      | The downstream GitHub branch to merge changes into.                                                                                                       |
| `--merge`              |       | Yes      | The GitHub branch where the merge will be written.                                                                                                        |
| `--bot-name`           |       | Yes      | The name used in git commits.                                                                                                                             |
| `--bot-email`          |       | Yes      | The email used in git commits.                                                                                                                            |
| `--working-dir`        |       | No       | The directory where repositories will be cloned. Defaults to `.`.                                                                                         |
| `--github-token`       |       | No*      | The path to a file containing a GitHub Personal Access Token. Alternative to GitHub App auth.                                                              |
| `--github-app-id`      |       | No       | The GitHub App ID. Defaults to `118774`.                                                                                                                  |
| `--github-app-key`     |       | No*      | The path to the GitHub App private key.                                                                                                                   |
| `--github-cloner-id`   |       | No       | The GitHub cloner App ID. Defaults to `121614`.                                                                                                           |
| `--github-cloner-key`  |       | No*      | The path to the GitHub cloner App private key.                                                                                                            |
| `--slack-webhook`      |       | No       | Path to Slack webhook credentials for notifications.                                                                                                      |
| `--update-go-modules`  |       | No       | Update and vendor Go modules in a separate commit.                                                                                                        |
| `--run-make`           |       | No       | Run `make merge-bot` and include changes in a separate commit.                                                                                           |

\* Either `--github-token` or both `--github-app-key` and `--github-cloner-key` must be provided.

### Examples

1. **Basic Merge**:
   ```bash
   python merge-bot.py --source https://example.com/upstream.git:main --dest my-org/my-repo:dev --merge my-org/my-repo:merge \
       --bot-name "Merge Bot" --bot-email "bot@example.com" --github-app-key /path/to/app.key \
       --github-cloner-key /path/to/cloner.key
   ```

2. **Merge with Go Modules Update**:
   ```bash
   python merge-bot.py --source https://example.com/upstream.git:main --dest my-org/my-repo:dev --merge my-org/my-repo:merge \
       --bot-name "Merge Bot" --bot-email "bot@example.com" --github-app-key /path/to/app.key \
       --github-cloner-key /path/to/cloner.key --update-go-modules
   ```

3. **Basic Merge with Personal Access Token**:
   ```bash
   python merge-bot.py --source https://example.com/upstream.git:main --dest my-org/my-repo:dev --merge my-org/my-repo:merge \
       --bot-name "Merge Bot" --bot-email "bot@example.com" --github-token /path/to/token
   ```

4. **Merge and Run `make`**:
   ```bash
   python merge-bot.py --source https://example.com/upstream.git:main --dest my-org/my-repo:dev --merge my-org/my-repo:merge \
       --bot-name "Merge Bot" --bot-email "bot@example.com" --github-app-key /path/to/app.key \
       --github-cloner-key /path/to/cloner.key --run-make
   ```

## Contributing

Contributions are welcome! Please submit a pull request or open an issue to discuss any changes or features.

---

For any questions or support, feel free to contact the maintainers via a Github issue.
