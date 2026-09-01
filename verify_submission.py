"""兼容旧的根目录离线自检命令。"""

from verification.verify_submission import main, verify_submission_manifest


__all__ = ["main", "verify_submission_manifest"]


if __name__ == "__main__":
    main()
