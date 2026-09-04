import os

password = "hunter2"

def run(cmd: str) -> None:
    os.system("echo " + cmd)

run(input("cmd: "))
