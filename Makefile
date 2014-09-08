default:
	@echo "One of:"
	@echo "    make lint"

test: lint

lint:
	@echo "Lint check (flake8)"
	@flake8 -v \
	    --exclude hooks/charmhelpers,hooks/_trial_temp \
	    hooks tests

sync:
	@bzr cat \
	    lp:charm-helpers/tools/charm_helpers_sync/charm_helpers_sync.py \
	    > .charm_helpers_sync.py
	@python .charm_helpers_sync.py -c charm-helpers-sync.yaml
	@rm .charm_helpers_sync.py
