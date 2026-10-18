PY ?= $(shell [ -x $(HOME)/.venvs/crowdshield/bin/python ] && echo $(HOME)/.venvs/crowdshield/bin/python || echo python3)

.PHONY: test test-ui demo smoke doctor validate results parity

test:            ## all Python tests (engine + gateway)
	$(PY) -m pytest -q

test-ui:         ## UI pure functions (needs Node.js)
	node --test tests/ui/ui.test.mjs

demo:            ## PipeGen video run -> engine -> gateway -> browser
	bash scripts/demo.sh video

smoke:           ## no camera: replay the sample stream through the gateway
	bash scripts/smoke_test.sh

doctor:
	python3 scripts/doctor.py --write docs/ENVIRONMENT.md

validate:        ## engine vs the reference numbers on the synthetic variants
	$(PY) tools/validate_variants.py --json docs/validation_variants.json

parity:          ## regenerate the browser-simulator parity recording
	node tools/make_parity.mjs

results:
	$(PY) -m crowdshield.make_results
