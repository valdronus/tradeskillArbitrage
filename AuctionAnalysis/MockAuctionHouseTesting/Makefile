# Makefile for MockDBData Synthetic Generation & Validation Framework

PYTHON ?= python3
PYTHON_SOURCES := generate.py validate.py test_mock_framework.py
DATA_DIR ?= ./data
DIFF_SCRIPT ?= ../AuctionScanDiff.py
SALES_DB ?= ./sales.db
GROUND_TRUTH ?= $(DATA_DIR)/ground_truth.json
REPORT ?= ./validation_report.md
SAMPLE ?= all

.ONESHELL:

.PHONY: usage help fmt lint lint-fix generate all 7pm evening_cluster long_gaps \
        generate-all generate-7pm generate-evening-cluster generate-long-gaps \
        validate sweep test clean

usage: help

help:
	@echo "MockDBData Test Framework Commands:"
	@echo "  make generate SAMPLE=all     Generate synthetic snapshots (schedule profile: all)"
	@echo "  make generate SAMPLE=7pm     Generate synthetic snapshots (schedule profile: 7pm)"
	@echo "  make all                     Generate synthetic snapshots and validate them against AuctionScanDiff" 
	@echo "  make 7pm                     Generate synthetic snapshots using the '7pm' profile"
	@echo "  make generate-all            Alias for 'make all'"
	@echo "  make generate-7pm            Alias for 'make 7pm'"
	@echo "  make generate-evening-cluster Generate evening cluster snapshots"
	@echo "  make generate-long-gaps      Generate long-gap expiration snapshots"
	@echo "  make validate                Run AuctionScanDiff and validate results against ground truth"
	@echo "  make sweep                   Run continuous hyperparameter grid search sweep"
	@echo "  make test                    Run barebones architecture sanity unit tests"
	@echo "  make clean                   Remove generated snapshot data and temporary files"

# Support 'make generate' with SAMPLE variable, or 'make generate all' / 'make generate 7pm'
fmt:
	$(PYTHON) -m black $(PYTHON_SOURCES)

lint:
	$(PYTHON) -m flake8 $(PYTHON_SOURCES)

lint-fix:
	$(PYTHON) -m ruff check --fix $(PYTHON_SOURCES)

generate:
	@echo "Generating synthetic snapshot data for SAMPLE=$(SAMPLE)..."
	@$(PYTHON) generate.py --schedule $(SAMPLE) --output-dir $(DATA_DIR) || {
		echo >&2 "ERROR: generate failed for SAMPLE=$(SAMPLE)."
		echo >&2 "Reproduce with: make generate SAMPLE=$(SAMPLE)"
		echo >&2 "Explore deeper with: $(PYTHON) generate.py --schedule $(SAMPLE) --output-dir $(DATA_DIR)"
		exit 1
	}

all: fmt generate validate

7pm:
	@echo "Generating synthetic data using schedule '7pm'..."
	@$(PYTHON) generate.py --schedule 7pm --output-dir $(DATA_DIR) || {
		echo >&2 "ERROR: 7pm generation failed."
		echo >&2 "Reproduce with: make 7pm"
		echo >&2 "Explore deeper with: $(PYTHON) generate.py --schedule 7pm --output-dir $(DATA_DIR)"
		exit 1
	}

evening_cluster:
	$(PYTHON) generate.py --schedule evening_cluster --output-dir $(DATA_DIR)

long_gaps:
	$(PYTHON) generate.py --schedule long_gaps --output-dir $(DATA_DIR)

generate-all: all

generate-7pm: 7pm

generate-evening-cluster: evening_cluster

generate-long-gaps: long_gaps

validate:
	@echo "Validating generated snapshots against AuctionScanDiff..."
	@$(PYTHON) validate.py \
		--snapshot-dir $(DATA_DIR) \
		--ground-truth $(GROUND_TRUTH) \
		--diff-script $(DIFF_SCRIPT) \
		--sales-db $(SALES_DB) \
		--report $(REPORT) || {
		echo >&2 "ERROR: validate failed."
		echo >&2 "Reproduce with: make validate"
		echo >&2 "Explore deeper with: $(PYTHON) validate.py --snapshot-dir $(DATA_DIR) --ground-truth $(GROUND_TRUTH) --diff-script $(DIFF_SCRIPT) --sales-db $(SALES_DB) --report $(REPORT)"
		exit 1
	}

sweep:
	@echo "Running hyperparameter sweep for validation..."
	@$(PYTHON) validate.py \
		--snapshot-dir $(DATA_DIR) \
		--ground-truth $(GROUND_TRUTH) \
		--diff-script $(DIFF_SCRIPT) \
		--sales-db $(SALES_DB) \
		--sweep || {
		echo >&2 "ERROR: sweep failed."
		echo >&2 "Reproduce with: make sweep"
		echo >&2 "Explore deeper with: $(PYTHON) validate.py --snapshot-dir $(DATA_DIR) --ground-truth $(GROUND_TRUTH) --diff-script $(DIFF_SCRIPT) --sales-db $(SALES_DB) --sweep"
		exit 1
	}

test:
	@echo "Running barebones framework sanity tests..."
	@$(PYTHON) test_mock_framework.py || {
		echo >&2 "ERROR: tests failed."
		echo >&2 "Reproduce with: make test"
		echo >&2 "Explore deeper with: $(PYTHON) test_mock_framework.py"
		echo >&2 "Inspect the entrypoint in test_mock_framework.py for targeted debugging."
		exit 1
	}

clean:
	rm -rf $(DATA_DIR) $(SALES_DB) $(REPORT) __pycache__ *.pyc
