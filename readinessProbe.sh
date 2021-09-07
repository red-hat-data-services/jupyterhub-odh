#!/bin/bash

function is_container_running() {
    [ $(curl -s -o /dev/null -w ''%{http_code}'' localhost:8081) == "302" ]
};

function is_leader_election_ready() {
    [ $(curl -s -o /dev/null -w ''%{http_code}'' localhost:4040) == "200" ]
}

function get_leader() {
    echo "$(curl http://localhost:4040 2> /dev/null | python3 -c "import sys, json; print(json.load(sys.stdin)['name'])")"
}

function get_pod_name() {
    echo ${HOSTNAME}
}

function is_container_leader() {
    [ "$(get_leader)" == "$(get_pod_name)" ]
};

if ! is_leader_election_ready; then
    # Leader election is not ready yet
    exit 0
fi

if ! is_container_running; then
    # Pod is waiting to become leader
    exit 0
elif is_container_leader; then
    # Pod is the leader
    exit 0
else
    # Pod is still running but it is not the leader
    exit 1
fi
