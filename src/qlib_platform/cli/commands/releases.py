"""Register CLI commands owned by the releases domain."""


def register(sub) -> None:
    release = sub.add_parser("release")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    release_sub.add_parser("list")
    release_verify = release_sub.add_parser("verify")
    release_verify.add_argument("reference")
    release_verify.add_argument("--mode", choices=["manifest", "sampled", "deep"], default="deep")
    release_verify.add_argument("--sample-size", type=int, default=64)
    release_verify.add_argument("--reuse-receipt", action="store_true")
    release_verify.add_argument("--workers", type=int, default=4)
    release_import = release_sub.add_parser("import-qlib")
    release_import.add_argument("--path", required=True)
    release_build_local = release_sub.add_parser("build-local")
    release_build_local.add_argument("--start")
    release_build_local.add_argument("--end")
    release_build_tushare = release_sub.add_parser("build-tushare")
    release_build_tushare.add_argument("--start", required=True)
    release_build_tushare.add_argument("--end", required=True)
    release_promote = release_sub.add_parser("promote")
    release_promote.add_argument("reference")
    release_promote.add_argument("--alias", default="research-release-current")
