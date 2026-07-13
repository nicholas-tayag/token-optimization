from agenvantage.tokenizer import TokenCounter


def test_count_treats_special_token_sentinels_as_repository_text() -> None:
    counter = TokenCounter()

    assert counter.count("The literal token is <|endoftext|>.") > 0
