# Note we do not enable testing appengine mail on python3, since the
# appengine libs are python2-only.
check: dev_deps
	echo "------ PYTHON2"
	cd tests && env PYTHONPATH=.. python2 -m unittest discover -p '*_test.py'
	echo "------ PYTHON3"
	cd tests && env PYTHONPATH=.. python3 -m unittest discover -p '*_test.py'

deps:
	pip install -r requirements.txt

dev_deps:
	pip install -r dev_requirements.txt
