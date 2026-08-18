PYTHON_SCRIPTS := AuctionAnalysis/extract_item_market_data.py test_extract_item_market_data.py

.PHONY: format lint check test generate validate all clean

format:
	black $(PYTHON_SCRIPTS)

lint:
	flake8 $(PYTHON_SCRIPTS)

check: format lint

test:
	python3 -m AuctionAnalysis.extract_item_market_data --self-test

generate:
	rm -rf AuctionAnalysis/MockDBData/data
	mkdir -p AuctionAnalysis/MockDBData/data
	python3 AuctionAnalysis/MockDBData/generate.py --output-dir AuctionAnalysis/MockDBData/data --items 10 --sellers 20 --buyers 40 --seed 123 --enable-row-duplicates

validate:
	python3 AuctionAnalysis/MockDBData/validate.py --snapshot-dir AuctionAnalysis/MockDBData/data --ground-truth AuctionAnalysis/MockDBData/data/ground_truth.json --diff-script AuctionAnalysis/AuctionScanDiff.py --sales-db AuctionAnalysis/MockDBData/data/sales.db --report AuctionAnalysis/MockDBData/data/validation_report.md

clean:
	rm -rf __pycache__ */__pycache__ **/__pycache__
	rm -rf *.pyc *.pyo *.pyd *.egg-info build dist
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -f *.log *.zip
	rm -rf AuctionAnalysis/MockDBData/data

all: validate