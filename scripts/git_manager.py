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
# Setup Logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
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
    repo_url = config.get("repo_url")
    if repo_url:
        branch = config.get("branch", "main")
        
        # Ensure remote URL has PAT (redundant safety check)
        github_pat = config.get("github_pat")
        if repo_url.startswith("https://") and github_pat and github_pat != "YOUR_GITHUB_PAT_HERE":
             authenticated_repo_url = repo_url.replace("https://", f"https://oauth2:{github_pat}@")
             run_git_cmd(user_dir, ["remote", "set-url", "origin", authenticated_repo_url], "Ensure remote URL has PAT")
        
        # Explicit push
        success, output = run_git_cmd(user_dir, ["push", "origin", branch], f"Push to {branch}")
        if success:
            logger.info(f"Push output: {output}")
            return True, "Success"
        else:
             logger.error(f"Push failed: {output}")
             return False, f"Push failed: {output}"
    else:
        logger.info(f"No remote URL for user {user_id}. Changes committed locally.")
        return True, "Committed locally"

def initialize_repo_structure(user_id):
    """
    Checks if the user's repo has the required structure.
    If not, creates default directories and pushes to remote.
    Returns: (success, message)
    """
    user_dir = os.path.join(USERS_ROOT, f"user_{user_id}")
    if not os.path.exists(user_dir):
        logger.error(f"User directory {user_dir} does not exist. Cannot initialize.")
        return False, "User directory not found"

    # Check for critical directories
    tasks_dir = os.path.join(user_dir, "tasks")
    if os.path.exists(tasks_dir):
        logger.info(f"Repo for user {user_id} seems already initialized.")
        return True, "Already initialized"

    logger.info(f"Initializing new repo structure for user {user_id}...")
    
    # Create structure
    os.makedirs(os.path.join(user_dir, "tasks", "archive"), exist_ok=True)
    os.makedirs(os.path.join(user_dir, "tasks", "recurrent"), exist_ok=True)
    os.makedirs(os.path.join(user_dir, "memories"), exist_ok=True)
    os.makedirs(os.path.join(user_dir, "instructions"), exist_ok=True)
    os.makedirs(os.path.join(user_dir, "mcp-servers"), exist_ok=True)
    os.makedirs(os.path.join(user_dir, "skills"), exist_ok=True)
    
    # Create some .gitkeep files to ensure directories are tracked
    for folder in ["tasks", "memories", "instructions", "mcp-servers", "skills"]:
        with open(os.path.join(user_dir, folder, ".gitkeep"), "w") as f:
            f.write("")
            
    # Create default skills.md if it doesn't exist
    skills_md_path = os.path.join(user_dir, "skills", "skills.md")
    if not os.path.exists(skills_md_path):
        with open(skills_md_path, "w") as f:
            f.write(
                "# Skills\n\n"
                "This directory contains skills that extend the assistant's capabilities.\n"
                "Each skill should be in its own subdirectory with a `SKILL.md` file describing it.\n"
            )
            
    # Create default Gemini config
    gemini_dir = os.path.join(user_dir, ".gemini")
    os.makedirs(gemini_dir, exist_ok=True)
    
    settings_path = os.path.join(gemini_dir, "settings.json")
    if not os.path.exists(settings_path):
        try:
            with open(settings_path, "w") as f:
                json.dump({
                    "security": {
                        "auth": {
                            "selectedType": "oauth-personal"
                        }
                    },
                    "mcpServers": {} 
                }, f, indent=2)
            logger.info(f"Created default .gemini/settings.json for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to create settings.json for user {user_id}: {e}")
    else:
        # Validate existing config (Self-Healing)
        try:
            with open(settings_path, "r") as f:
                current_settings = json.load(f)
            
            changed = False
            # Fix mcpServers type
            if "mcpServers" in current_settings and isinstance(current_settings["mcpServers"], list):
                logger.warning(f"Found malformed mcpServers (list) for user {user_id}. Fixing to dict.")
                # If it's a list, we can't easily convert to dict without keys, so reset to empty dict 
                # or try to preserve if it was a list of objects with names? 
                # For now, safer to reset or just make it empty if empty list
                current_settings["mcpServers"] = {}
                changed = True
            
            if changed:
                with open(settings_path, "w") as f:
                    json.dump(current_settings, f, indent=2)
                logger.info(f"Self-healed settings.json for user {user_id}")
                
        except Exception as e:
             logger.error(f"Failed to validate settings.json for user {user_id}: {e}")

    # Create .gitignore
    gitignore_path = os.path.join(user_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        with open(gitignore_path, "w") as f:
            f.write(
                "secrets/\n"
                ".env\n"
                "**/token.json\n"
                "**/credentials.json\n"
                ".gemini/*\n"
                "!.gemini/settings.json\n"
                "__pycache__/\n"
                "*.log\n"
            )
            
    # Commit and push
    return commit_and_push(user_id, "Initialize Assistant folder structure")

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
        success, out = commit_and_push(uid, msg)
        print(f"Commit result: {success} - {out}")
        
    else:
        print("Usage: python git_manager.py [restore|commit]")
