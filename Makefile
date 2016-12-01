default:
	@echo "One of:"
	@echo "    make testdeps"
	@echo "    make lint"
	@echo "    make integration"

test: testdeps lint integration

testdeps:
	sudo apt install -y amulet flake8 python3-psycopg2

integration:
	tests/test_integration.py -v

lint:
	@echo "Lint check (flake8)"
	flake8 -v reactive tests
	charm proof
