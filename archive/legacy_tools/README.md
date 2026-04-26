# Legacy Tools Archive

这些脚本已从主路径移出，只作历史参考或临时手动迁移使用。

- `aw_bridge.py`：旧 ActivityWatch 本地轮询桥，尚未接入当前 plugin/MQTT 路径。
- `extract_events.py` / `build_triplets.py`：旧训练语料抽取与 triplet 构造脚本，依赖已删除的 `training_data/` 快照。
- `check_isp.sh`：旧 ISP/root 路径检查脚本，内容与当前 `fiet` 用户部署拓扑不完全一致。
- `sync_store.sh`：旧 cron 自动提交 `store/` 的脚本；当前 store/training data 作为运行时数据处理。

需要重新启用时，先按当前 `plugins/`、MQTT topic 和 `FiamConfig` 路径重新审查，不要直接当作生产入口。