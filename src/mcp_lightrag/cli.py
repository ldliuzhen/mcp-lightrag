"""
Command-line interface for the LightRAG MCP server.
"""

import argparse
import logging
import sys
import os

from .mcp_tools import mcp

def setup_logging(level: str = "INFO"):
    """Configure structured logging for the server."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)]
    )

def main():
    """Entry point for starting the MCP server."""
    parser = argparse.ArgumentParser(description="LightRAG MCP Server - Bridge between MCP and LightRAG API")
    parser.add_argument("--url", help="Full LightRAG API base URL, e.g. http://localhost:9621")
    parser.add_argument("--host", help="LightRAG API host")
    parser.add_argument("--port", type=int, help="LightRAG API port")
    parser.add_argument("--api-key", help="Optional API key for authentication")
    parser.add_argument("--username", help="Optional LightRAG username for OAuth login")
    parser.add_argument("--password", help="Optional LightRAG password for OAuth login")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Set logging verbosity")
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("mcp_lightrag")
    
    # Override environment variables if CLI args are provided
    if args.url:
        os.environ["LIGHTRAG_URL"] = args.url
    if args.host:
        os.environ["LIGHTRAG_HOST"] = args.host
    if args.port:
        os.environ["LIGHTRAG_PORT"] = str(args.port)
    if args.api_key:
        os.environ["LIGHTRAG_API_KEY"] = args.api_key
    if args.username:
        os.environ["LIGHTRAG_USERNAME"] = args.username
    if args.password:
        os.environ["LIGHTRAG_PASSWORD"] = args.password
        
    logger.info("Initializing LightRAG MCP Server...")
    
    try:
        # Run using stdio transport as default for MCP
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Server stopped by user signal")
    except Exception as e:
        logger.exception(f"Critical failure during server execution: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
