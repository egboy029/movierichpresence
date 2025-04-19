import os
import sys
import subprocess
import shutil

def check_python_version():
    """Check if the Python version is compatible."""
    print("Checking Python version...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 7):
        print(f"Error: Python 3.7+ is required. You have Python {version.major}.{version.minor}.{version.micro}")
        return False
    print(f"Python version {version.major}.{version.minor}.{version.micro} is compatible.")
    return True

def install_requirements():
    """Install the required packages."""
    print("Installing requirements...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("Requirements installed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error installing requirements: {e}")
        return False

def check_env_file():
    """Check if .env file exists, if not create from example."""
    if os.path.exists(".env"):
        print(".env file already exists.")
        return True
    
    if os.path.exists(".env.example"):
        print(".env file not found. Creating from .env.example...")
        try:
            shutil.copy(".env.example", ".env")
            print(".env file created. Please edit it with your API keys.")
            print("Open the .env file and replace the placeholder values with your actual API keys.")
            return True
        except Exception as e:
            print(f"Error creating .env file: {e}")
            return False
    else:
        print("Neither .env nor .env.example found. Creating basic .env file...")
        try:
            with open(".env", "w") as f:
                f.write("DISCORD_CLIENT_ID=your_discord_application_id_here\n")
                f.write("TMDB_API_KEY=your_tmdb_api_key_here\n")
            print(".env file created. Please edit it with your API keys.")
            return True
        except Exception as e:
            print(f"Error creating .env file: {e}")
            return False

def main():
    """Main setup function."""
    print("=" * 50)
    print("Discord Rich Presence for Disney+ & Netflix Setup")
    print("=" * 50)
    
    # Check if Python version is compatible
    if not check_python_version():
        input("Press Enter to exit...")
        return
    
    # Install requirements
    if not install_requirements():
        input("Press Enter to exit...")
        return
    
    # Check .env file
    if not check_env_file():
        input("Press Enter to exit...")
        return
    
    print("\nSetup completed successfully!")
    print("\nWhat to do next:")
    print("1. Edit the .env file with your Discord Client ID and TMDB API Key")
    print("2. Run the application using start.bat or python discord_presence.py")
    print("\nFor more information, check the README.md file.")
    
    input("Press Enter to exit...")

if __name__ == "__main__":
    main() 