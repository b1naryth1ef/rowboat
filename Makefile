up:
	docker-compose up -d

down:
	docker-compose down

cli:
	docker-compose exec bot /bin/bash

worker-logs:
	docker-compose exec workers tail -F worker-0.log
