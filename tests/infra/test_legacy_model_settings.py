from atlas.infra.config import Settings


def test_legacy_free_labels_migrate_to_honest_model_ids() -> None:
    settings = Settings(_env_file=None, default_model="glm-5.2-free", heavy_model="minimax-m3-free")
    assert settings.default_model == "glm-5.2"
    assert settings.heavy_model == "minimax-m3"
