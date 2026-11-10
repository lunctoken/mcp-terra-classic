"""Run Terra Classic MCP over stdio for local clients."""

from terra_classic_mcp.app import build_server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
