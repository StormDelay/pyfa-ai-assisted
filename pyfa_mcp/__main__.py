import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pyfa_mcp.server import main
    main()
