default:
	@echo "One of:"
	@echo "    make lint"

test: lint

lint:
	@echo "Lint check (flake8)"
	@flake8 -v \
	    --exclude hooks/charmhelpers,hooks/_trial_temp \
	    hooks tests
