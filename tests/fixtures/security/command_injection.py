"""File processing utilities — contains command injection vulnerabilities."""
import subprocess
import os


def count_lines(filename: str) -> int:
    # VULN: shell=True + user-controlled filename
    result = subprocess.run(
        f"wc -l {filename}", shell=True, capture_output=True, text=True
    )
    return int(result.stdout.split()[0]) if result.stdout.strip() else 0


def convert_image(input_path: str, output_format: str) -> bool:
    # VULN: both args user-controlled, no sanitization
    exit_code = os.system(f"convert {input_path} output.{output_format}")
    return exit_code == 0


def tail_logs(log_file: str, lines: int = 100) -> str:
    # VULN: log_file not validated, shell=True
    cmd = f"tail -n {lines} {log_file}"
    return subprocess.check_output(cmd, shell=True, text=True)


def compress_directory(dir_path: str, archive_name: str) -> None:
    # VULN: directory path injected directly
    os.system(f"tar -czf /tmp/{archive_name}.tar.gz {dir_path}")


def ping_host(host: str) -> str:
    # VULN: classic OS command injection
    result = subprocess.run(
        f"ping -c 1 {host}", shell=True, capture_output=True, text=True
    )
    return result.stdout
