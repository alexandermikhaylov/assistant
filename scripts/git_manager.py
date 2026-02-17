import os
import json
import subprocess
import logging
from datetime import datetime

# Configure
LOG_FILE = "/app/data/logs/git_manager.log"
USER_REGISTRY_FILE = "/app/config/user_registry.json"
USERS_ROOT = "/app/users"

# Setup Logger
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("GitManager")

def load_registry():
    try:
        with open(USER_REGISTRY_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Registry file not found at {USER_REGISTRY_FILE}")
        return {}
    except Exception as e:
        logger.error(f"Error loading registry: {e}")
        return {}

def run_git_cmd(cwd, args, description="git command"):
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Success: {description} in {cwd}")
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed: {description} in {cwd}. Error: {e.stderr}")
        return False, e.stderr

def setup_user_repo(user_id, config):
    user_dir = os.path.join(USERS_ROOT, f"user_{user_id}")
    repo_url = config.get("repo_url")
    branch = config.get("branch", "main")
    github_pat = config.get("github_pat") # Get PAT from config
    
    if not repo_url:
        logger.info(f"No repo URL for user {user_id}. Skipping remote sync.")
        return

    # Construct the authenticated URL for HTTPS if PAT is provided
    authenticated_repo_url = repo_url
    if repo_url.startswith("https://") and github_pat and github_pat != "YOUR_GITHUB_PAT_HERE":
        # Assuming format is https://github.com/owner/repo.git
        # Insert PAT before github.com
        authenticated_repo_url = repo_url.replace("https://", f"https://oauth2:{github_pat}@")
        logger.info(f"Using PAT for HTTPS authentication for user {user_id}.")

    # Check if directory exists
    if os.path.exists(user_dir):
        # Verify if it's a git repo
        if os.path.isdir(os.path.join(user_dir, ".git")):
            logger.info(f"Repo exists for user {user_id}. Pulling latest changes...")
            # Ensure the remote URL is set with PAT for pull
            success, _ = run_git_cmd(user_dir, ["remote", "set-url", "origin", authenticated_repo_url], "Set remote URL with PAT")
            if not success:
                logger.error(f"Failed to set remote URL for user {user_id}.")
                return
            run_git_cmd(user_dir, ["pull", "origin", branch], "Pull latest changes")
        else:
            logger.warning(f"Directory {user_dir} exists but is not a git repo. Skipping clone.")
    else:
        # Clone
        logger.info(f"Cloning repo for user {user_id} from {authenticated_repo_url}...")
        parent_dir = os.path.dirname(user_dir)
        os.makedirs(parent_dir, exist_ok=True)
        
        success, _ = run_git_cmd(parent_dir, ["clone", "-b", branch, authenticated_repo_url, f"user_{user_id}"], f"Clone repo for {user_id}")
        
        if success:
            # Configure local user
            run_git_cmd(user_dir, ["config", "user.name", config.get("git_username", "Assistant Bot")], "Config user.name")
            run_git_cmd(user_dir, ["config", "user.email", config.get("git_email", "bot@assistant.ai")], "Config user.email")

def commit_and_push(user_id, commit_message):
    user_dir = os.path.join(USERS_ROOT, f"user_{user_id}")
    registry = load_registry()
    config = registry.get(str(user_id), {})
    
    if not os.path.exists(os.path.join(user_dir, ".git")):
        logger.warning(f"User {user_id} directory is not a git repo. Skipping commit.")
        return

    # Add all changes
    run_git_cmd(user_dir, ["add", "."], "Stage changes")
    
    # Commit
    run_git_cmd(user_dir, ["commit", "-m", commit_message], "Commit changes")
    
    # Push (only if remote is configured)
    if config.get("repo_url"):
        run_git_cmd(user_dir, ["push"], "Push changes")
    else:
        logger.info(f"No remote URL for user {user_id}. Changes committed locally.")

if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "help"
    
    if action == "restore":
        registry = load_registry()
        for uid, cfg in registry.items():
            setup_user_repo(uid, cfg)
            
    elif action == "commit":
        if len(sys.argv) < 3:
            print("Usage: commit <user_id> [message]")
            sys.exit(1)
        uid = sys.argv[2]
        msg = sys.argv[3] if len(sys.argv) > 3 else f"Update {datetime.now().isoformat()}"
        commit_and_push(uid, msg)
        
    else:
        print("Usage: python git_manager.py [restore|commit]")
