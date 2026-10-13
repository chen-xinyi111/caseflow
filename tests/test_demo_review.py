import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('demo_review',Path('scripts/validate_collaboration.py'))
review=importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

def test_review_allows_demo_uris_and_blocks_identifiers():
    review.check_review_content('redis://redis:6379/0 https://example.invalid /var/run/postgresql')
    for value in [r'C:\Users\person\private.txt','D:/private/data','person@example.com','sk-'+'x'*20,'fixture-credential']:
        with pytest.raises(AssertionError):review.check_review_content(value,['fixture-credential'])
