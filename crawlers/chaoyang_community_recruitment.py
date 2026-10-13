"""Discover Chaoyang community-worker recruitment across years and pages."""

if __package__:
    from .lib.community_recruitment import CHAOYANG, main
else:
    from lib.community_recruitment import CHAOYANG, main


if __name__ == "__main__":
    raise SystemExit(main(CHAOYANG))
