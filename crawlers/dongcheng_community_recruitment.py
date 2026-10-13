"""Discover Dongcheng community-worker recruitment across years and pages."""

if __package__:
    from .lib.community_recruitment import DONGCHENG, main
else:
    from lib.community_recruitment import DONGCHENG, main


if __name__ == "__main__":
    raise SystemExit(main(DONGCHENG))
