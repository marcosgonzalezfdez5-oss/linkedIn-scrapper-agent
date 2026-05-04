"""
Run this once before using the agent:
    python setup.py
"""
import subprocess
import sys
import os
import re


def check_python_version():
    if sys.version_info < (3, 9):
        print(f"[ERROR] Python 3.9+ required. Found: {sys.version}")
        sys.exit(1)
    print(f"[OK] Python {sys.version.split()[0]}")


def install_requirements():
    req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
    print("[...] Installing dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req_file, "-q"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] pip install failed:\n{result.stderr}")
        sys.exit(1)
    print("[OK] Dependencies installed")


def load_api_key() -> str:
    key = os.environ.get("PARALLEL_API_KEY")
    if key:
        print("[OK] PARALLEL_API_KEY loaded from environment")
        return key

    key_env = os.path.join(os.path.dirname(__file__), "key.env")
    try:
        with open(key_env) as f:
            match = re.search(r"PARALLEL_API_KEY=(.+)", f.read())
            if match:
                key = match.group(1).strip()
                print(f"[OK] PARALLEL_API_KEY loaded from key.env ({key[:6]}...)")
                return key
    except FileNotFoundError:
        pass

    print("[ERROR] PARALLEL_API_KEY not found in environment or key.env")
    sys.exit(1)


def verify_parallel_connection(api_key: str):
    print("[...] Verifying Parallel API connection...")
    try:
        from parallel import Parallel
        client = Parallel(api_key=api_key)
        results = client.search(
            objective="Quick connectivity test",
            search_queries=["Stripe company"],
        )
        if results and results.results:
            print(f"[OK] Parallel API connected — got {len(results.results)} results")
        else:
            print("[WARN] Parallel API responded but returned no results")
    except Exception as e:
        print(f"[ERROR] Parallel API connection failed: {e}")
        sys.exit(1)


def print_usage():
    print()
    print("=" * 50)
    print("  Setup complete. Run the agent with:")
    print()
    print('    python -m company_intel_agent.main "Stripe"')
    print()
    print("  Or interactively:")
    print()
    print("    python -m company_intel_agent.main")
    print("=" * 50)


if __name__ == "__main__":
    print("\n--- Company Intel Agent Setup ---\n")
    check_python_version()
    install_requirements()
    api_key = load_api_key()
    verify_parallel_connection(api_key)
    print_usage()
