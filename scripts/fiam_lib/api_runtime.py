"""Manual API runtime command."""

from __future__ import annotations

import argparse

from fiam_lib.core import _build_config, _load_env_file, _project_root


def cmd_api(args: argparse.Namespace) -> None:
    """Call the configured API runtime once."""
    config = _build_config(args)
    _load_env_file(_project_root())

    from fiam.conductor import Conductor
    from fiam.runtime.api import ApiRuntime
    from fiam.runtime.recall import refresh_recall
    from fiam.store.features import FeatureStore
    from fiam.store.pool import Pool

    conductor = None
    if not getattr(args, "no_record", False):
        pool = Pool(config.pool_dir, dim=config.embedding_dim)
        feature_store = FeatureStore(config.feature_dir, dim=config.embedding_dim)
        try:
            from fiam.retriever.embedder import Embedder
            embedder = Embedder(config)
        except Exception:
            embedder = None

        def _refresh(vec):
            return refresh_recall(config, pool, vec, top_k=config.recall_top_k)

        conductor = Conductor(
            pool=pool,
            embedder=embedder,
            config=config,
            flow_path=config.flow_path,
            drift_threshold=config.drift_threshold,
            gorge_max_beat=config.gorge_max_beat,
            gorge_min_depth=config.gorge_min_depth,
            gorge_stream_confirm=config.gorge_stream_confirm,
            memory_mode=config.memory_mode,
            feature_store=feature_store,
        )
        runtime = ApiRuntime.from_config(
            config,
            conductor=conductor,
            recall_refresher=_refresh,
        )
    else:
        runtime = ApiRuntime.from_config(config)

    result = runtime.ask(
        args.text,
        source=getattr(args, "source", "cli") or "cli",
        record=not getattr(args, "no_record", False),
    )
    print(result.reply)
    if getattr(args, "debug", False):
        print(f"\n[api] model={result.model} usage={result.usage} recall={result.recall_fragments}")