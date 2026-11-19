"""显式初始化本机.env加密主密钥；不输出密钥、不迁移库或重启服务。"""

from pathlib import Path

from cryptography.fernet import Fernet


def initialize(path: Path):
    if not path.is_file():
        raise ValueError("Create the MiniMew .env first")
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [
        i for i, line in enumerate(lines) if line.startswith("MINIMEW_CREDENTIAL_MASTER_KEY=")
    ]
    if len(matches) > 1:
        raise ValueError("Duplicate credential master key entries")
    if matches:
        value = lines[matches[0]].split("=", 1)[1].strip().strip("\"'")
        if value:
            Fernet(value.encode("ascii"))
            return False
    line = "MINIMEW_CREDENTIAL_MASTER_KEY=" + Fernet.generate_key().decode()
    if matches:
        lines[matches[0]] = line
    else:
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init-key"])
    parser.parse_args()
    path = Path(__file__).resolve().parents[3] / ".env"
    print(
        "Master key initialized in local .env"
        if initialize(path)
        else "Existing master key preserved"
    )
