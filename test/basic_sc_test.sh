#! /bin/bash
 
DOCKER="$(which docker)"
VM_HOME="/test_framwork"
VOLUME="exa_basic_sc_test_volume"
IMAGE="exasol/docker-db:latest"
DO_PULL="true"
  
usage() {
    echo "Usage: $0 [-i IMAGE] [-D DOCKER CMD]"
    echo "Parameters:"
    echo "-i    : Docker image to use for the test (default: '$IMAGE')."
    echo "-D    : Docker command (default: '$DOCKER')."
}
   
log() { 
    echo "[$(date +"%Y-%m-%d %H:%M:%S.%3N")][$(basename ${0})]: ${*}"
}
     
die() { 
    log "FATAL: ${*}"
    exit 1
}
 
# parse parameters
while getopts "i:D:Ph" opt; do
    case "$opt" in
        i)
            IMAGE="$OPTARG"
            log "INFO:: Using image '$IMAGE'."
            ;;
        D)
            DOCKER="$OPTARG"
            log "INFO:: Using Docker command '$DOCKER'."
            ;;
        P)
            DO_PULL="false"
            log "INFO:: Not pulling the Docker image."
            ;;
        h)
            usage
            exit 0
            ;;
        ?)
            usage
            exit 1
            ;;
    esac
done
 
wait_db_start() {
    SEC_WAIT=120
    SEC_DONE=0
    log "Waiting $SEC_WAIT seconds for DB in container $1 to start... "
    while true 
    do
        if [[ $SEC_DONE -lt $SEC_WAIT ]]; then
            if [[ $(docker logs "$1" 2>&1 | grep -e ".*Started.*exasql .*") ]]; then
                log "successful!"
                return 0
            else
                sleep 1
                SEC_DONE=$((SEC_DONE+1))
            fi
        else
            log "ERROR: timed out. Collecting container logs:"
            "$DOCKER" logs "$1" 2>&1
            return 1
        fi
    done
}

if [[ "$DO_PULL" == "true" ]]; then
    $DOCKER pull "$IMAGE" #does not work with locally built dev-images
fi
set -e
log "=== Starting self-contained basic test ==="
log "= PART 1 : Creating container without a persistent volume ="
CONTAINER=$("$DOCKER" run --detach --privileged "$IMAGE") &&
wait_db_start "$CONTAINER" 
log "Checking ConfD connection..."
"$DOCKER" exec "$CONTAINER" /bin/bash -c 'examaster -c -n localhost' 2>&1 | tee /dev/stderr | grep localhost
log "Testing exaplus functionality"
"$DOCKER" exec "$CONTAINER" /bin/bash -c 'X=$(shopt -s nullglob; ls /usr/opt/EXASuite-*/EXASolution-*/bin/Console/exaplus /opt/exasol/db-*/bin/Console/exaplus | tail -n1); echo "SELECT 123*42345;" | $X -c n11:8563 -u sys -P exasol -jdbcparam validateservercertificate=0' 2>&1 | tee /dev/stderr | grep -q 5208435
log "Creating a file within the container"
"$DOCKER" exec "$CONTAINER" touch /exa/my_file
log "Stopping the container"
"$DOCKER" stop -t 60 "$CONTAINER"
log "Restarting the container"
"$DOCKER" start "$CONTAINER"
wait_db_start "$CONTAINER"
log "Checking if the file still exists... "
if [[ -z $("$DOCKER" exec "$CONTAINER" find /exa -name my_file) ]]; then
    die "File-check failed!"
fi
log "Stopping and deleting the container"
"$DOCKER" stop "$CONTAINER"
"$DOCKER" rm "$CONTAINER"

log "= PART 2 : Creating a new container with a persistent volume ="
if [[ ! -z $("$DOCKER" volume ls | grep "$VOLUME") ]]; then
    "$DOCKER" volume rm "$VOLUME"
fi
CONTAINER=$("$DOCKER" run --detach --privileged -v $VOLUME:/exa "$IMAGE") &&
wait_db_start "$CONTAINER" 
log "Testing exaplus functionality"
"$DOCKER" exec "$CONTAINER" /bin/bash -c 'X=$(shopt -s nullglob; ls /usr/opt/EXASuite-*/EXASolution-*/bin/Console/exaplus /opt/exasol/db-*/bin/Console/exaplus | tail -n1); echo "SELECT 123*42345;" | $X -c n11:8563 -u sys -P exasol -jdbcparam validateservercertificate=0' 2>&1 | tee /dev/stderr | grep -q 5208435
log "Creating a file within the container"
"$DOCKER" exec "$CONTAINER" touch /exa/my_file
log "Stopping the container"
"$DOCKER" stop -t 60 "$CONTAINER"
log "Restarting the container"
"$DOCKER" start "$CONTAINER"
wait_db_start "$CONTAINER"
log "Checking if the file still exists... "
if [[ -z $("$DOCKER" exec "$CONTAINER" find /exa -name my_file) ]]; then
    die "File-check failed!"
fi
log "Stopping and deleting the container"
"$DOCKER" stop "$CONTAINER"
"$DOCKER" rm "$CONTAINER"
log "Creating a new container with the same volume"
CONTAINER=$("$DOCKER" run --detach --privileged -v $VOLUME:/exa "$IMAGE") &&
wait_db_start "$CONTAINER" 
log "Checking if the file still exists... "
if [[ -z $("$DOCKER" exec "$CONTAINER" find /exa -name my_file) ]]; then
    die "File-check failed!"
fi 
log "Testing exaplus functionality"
"$DOCKER" exec "$CONTAINER" /bin/bash -c 'X=$(shopt -s nullglob; ls /usr/opt/EXASuite-*/EXASolution-*/bin/Console/exaplus /opt/exasol/db-*/bin/Console/exaplus | tail -n1); echo "SELECT 123*42345;" | $X -c n11:8563 -u sys -P exasol -jdbcparam validateservercertificate=0' 2>&1 | tee /dev/stderr | grep -q 5208435
log "Stopping and deleting the container"
"$DOCKER" stop "$CONTAINER"
"$DOCKER" rm "$CONTAINER"
log "Deleting the persistent volume"
"$DOCKER" volume rm "$VOLUME"
log "=== Successful! ==="
