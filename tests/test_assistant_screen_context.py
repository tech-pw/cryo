from cryo.ai.assistant.context import screen_block


def test_screen_block_names_the_open_table() -> None:
    text = screen_block(
        {
            "entities": [
                {
                    "qualified_name": "pw_experimentation.gold_unleash_experiments_v1",
                    "table_id": "abc",
                }
            ]
        },
        {"route": "/tables/abc", "page_heading": "gold_unleash_experiments_v1"},
    )
    assert "gold_unleash_experiments_v1" in text
    assert "Do not ask which table" in text
