#!/bin/bash

# SeaweedFS Component Startup Script
# Usage: ./start-seaweedfs-components.sh [options]
# 
# This script starts SeaweedFS components individually for testing:
# 1. Master server
# 2. Volume server(s)
# 3. Filer
# 4. S3 gateway (optional)
# 5. MQ broker (optional)

set -e

# Default configuration
MASTER_PORT=${MASTER_PORT:-9333}
VOLUME_PORT=${VOLUME_PORT:-8080}
FILER_PORT=${FILER_PORT:-8888}
S3_PORT=${S3_PORT:-8000}
MQ_PORT=${MQ_PORT:-17777}
METRICS_PORT=${METRICS_PORT:-9324}

DATA_DIR=${WEED_DATA_DIR:-"/tmp/seaweedfs-$(date +%s)"}
WEED_BINARY=${WEED_BINARY:-"weed"}
VERBOSE=${VERBOSE:-1}

# Component flags
START_MASTER=${START_MASTER:-true}
START_VOLUME=${START_VOLUME:-true}
START_FILER=${START_FILER:-true}
START_S3=${START_S3:-false}
START_MQ=${START_MQ:-false}

# Advanced options
VOLUME_MAX=${VOLUME_MAX:-100}
VOLUME_SIZE_LIMIT=${VOLUME_SIZE_LIMIT:-100}
FILER_MAX_MB=${FILER_MAX_MB:-64}
USE_RAFT=${USE_RAFT:-true}
CLEANUP_ON_EXIT=${CLEANUP_ON_EXIT:-true}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Function to wait for a service to be ready
wait_for_service() {
    local service_name=$1
    local host=$2
    local port=$3
    local max_attempts=${4:-30}
    local check_type=${5:-"http"} # http or tcp
    
    log_step "Waiting for $service_name to be ready on $host:$port..."
    
    for i in $(seq 1 $max_attempts); do
        if [ "$check_type" = "http" ]; then
            if curl -s "http://$host:$port/" > /dev/null 2>&1 || curl -s "http://$host:$port/status" > /dev/null 2>&1; then
                log_info "✅ $service_name is ready"
                return 0
            fi
        else
            if nc -z $host $port 2>/dev/null; then
                log_info "✅ $service_name is ready"
                return 0
            fi
        fi
        
        echo "  Waiting for $service_name... ($i/$max_attempts)"
        sleep 2
    done
    
    log_error "❌ $service_name failed to start within $(($max_attempts * 2)) seconds"
    return 1
}

# Function to start master server
start_master() {
    log_step "Starting SeaweedFS Master Server..."
    
    local raft_flag=""
    if [ "$USE_RAFT" = "true" ]; then
        raft_flag="-raftHashicorp"
    fi
    
    nohup $WEED_BINARY -v $VERBOSE master \
        -port=$MASTER_PORT \
        -mdir="$DATA_DIR/master" \
        $raft_flag \
        -electionTimeout=1s \
        -volumeSizeLimitMB=$VOLUME_SIZE_LIMIT \
        -ip="127.0.0.1" \
        -ip.bind="0.0.0.0" \
        > "$DATA_DIR/master.log" 2>&1 &
    
    echo $! > "$DATA_DIR/master.pid"
    
    # Wait for master to be ready
    if ! wait_for_service "Master" "127.0.0.1" "$MASTER_PORT" 30 "http"; then
        log_error "Master failed to start. Showing last 50 lines of log:"
        tail -50 "$DATA_DIR/master.log" || echo "Could not read master log"
        return 1
    fi
    
    # Also wait for gRPC port (master port + 10000)
    local grpc_port=$((MASTER_PORT + 10000))
    if ! wait_for_service "Master gRPC" "127.0.0.1" "$grpc_port" 30 "tcp"; then
        log_error "Master gRPC failed to start. Showing last 50 lines of log:"
        tail -50 "$DATA_DIR/master.log" || echo "Could not read master log"
        return 1
    fi
}

# Function to start volume server
start_volume() {
    log_step "Starting SeaweedFS Volume Server..."
    
    mkdir -p "$DATA_DIR/volume"
    
    nohup $WEED_BINARY -v $VERBOSE volume \
        -port=$VOLUME_PORT \
        -dir="$DATA_DIR/volume" \
        -max=$VOLUME_MAX \
        -mserver="127.0.0.1:$MASTER_PORT" \
        -preStopSeconds=1 \
        -ip="127.0.0.1" \
        -ip.bind="0.0.0.0" \
        > "$DATA_DIR/volume.log" 2>&1 &
    
    echo $! > "$DATA_DIR/volume.pid"
    
    # Wait for volume server to be ready
    if ! wait_for_service "Volume Server" "127.0.0.1" "$VOLUME_PORT" 30 "http"; then
        log_error "Volume Server failed to start. Showing last 50 lines of log:"
        tail -50 "$DATA_DIR/volume.log" || echo "Could not read volume log"
        return 1
    fi
}

# Function to start filer
start_filer() {
    log_step "Starting SeaweedFS Filer..."
    
    mkdir -p "$DATA_DIR/filer"
    
    nohup $WEED_BINARY -v $VERBOSE filer \
        -port=$FILER_PORT \
        -defaultStoreDir="$DATA_DIR/filer" \
        -master="127.0.0.1:$MASTER_PORT" \
        -maxMB=$FILER_MAX_MB \
        -ip="127.0.0.1" \
        -ip.bind="0.0.0.0" \
        > "$DATA_DIR/filer.log" 2>&1 &
    
    echo $! > "$DATA_DIR/filer.pid"
    
    # Wait for filer to be ready
    if ! wait_for_service "Filer" "127.0.0.1" "$FILER_PORT" 30 "http"; then
        log_error "Filer failed to start. Showing last 50 lines of log:"
        tail -50 "$DATA_DIR/filer.log" || echo "Could not read filer log"
        return 1
    fi
}

# Function to start S3 gateway
start_s3() {
    log_step "Starting SeaweedFS S3 Gateway..."
    
    local s3_config=""
    if [ -n "$S3_CONFIG_FILE" ] && [ -f "$S3_CONFIG_FILE" ]; then
        s3_config="-config=$S3_CONFIG_FILE"
    fi
    
    nohup $WEED_BINARY -v $VERBOSE s3 \
        -port=$S3_PORT \
        -filer="127.0.0.1:$FILER_PORT" \
        -allowEmptyFolder=false \
        -allowDeleteBucketNotEmpty=true \
        $s3_config \
        -ip.bind="0.0.0.0" \
        > "$DATA_DIR/s3.log" 2>&1 &
    
    echo $! > "$DATA_DIR/s3.pid"
    
    # Wait for S3 gateway to be ready
    if ! wait_for_service "S3 Gateway" "127.0.0.1" "$S3_PORT" 30 "http"; then
        log_error "S3 Gateway failed to start. Showing last 50 lines of log:"
        tail -50 "$DATA_DIR/s3.log" || echo "Could not read s3 log"
        return 1
    fi
}

# Function to start MQ broker
start_mq() {
    log_step "Starting SeaweedFS MQ Broker..."
    
    nohup $WEED_BINARY -v $VERBOSE mq.broker \
        -port=$MQ_PORT \
        -master="127.0.0.1:$MASTER_PORT" \
        -ip="127.0.0.1" \
        -logFlushInterval=0 \
        > "$DATA_DIR/mq.log" 2>&1 &
    
    echo $! > "$DATA_DIR/mq.pid"
    
    # Wait for MQ broker to be ready
    wait_for_service "MQ Broker" "127.0.0.1" "$MQ_PORT" 30 "tcp"
    
    # Give broker additional time to register with master
    log_step "Allowing MQ broker to register with master..."
    sleep 15
}

# Function to show cluster status
show_status() {
    log_step "Checking cluster status..."
    
    echo "=== Cluster Status ==="
    curl -s "http://127.0.0.1:$MASTER_PORT/cluster/status" | jq . 2>/dev/null || curl -s "http://127.0.0.1:$MASTER_PORT/cluster/status"
    
    echo ""
    echo "=== Directory Status ==="
    curl -s "http://127.0.0.1:$MASTER_PORT/dir/status" | head -20
    
    echo ""
    echo "=== Running Processes ==="
    if [ -f "$DATA_DIR/master.pid" ]; then
        echo "Master PID: $(cat $DATA_DIR/master.pid)"
    fi
    if [ -f "$DATA_DIR/volume.pid" ]; then
        echo "Volume PID: $(cat $DATA_DIR/volume.pid)"
    fi
    if [ -f "$DATA_DIR/filer.pid" ]; then
        echo "Filer PID: $(cat $DATA_DIR/filer.pid)"
    fi
    if [ -f "$DATA_DIR/s3.pid" ]; then
        echo "S3 PID: $(cat $DATA_DIR/s3.pid)"
    fi
    if [ -f "$DATA_DIR/mq.pid" ]; then
        echo "MQ PID: $(cat $DATA_DIR/mq.pid)"
    fi
}

# Function to stop all components
stop_all() {
    log_step "Stopping all SeaweedFS components..."
    
    for component in mq s3 filer volume master; do
        if [ -f "$DATA_DIR/$component.pid" ]; then
            local pid=$(cat "$DATA_DIR/$component.pid")
            if kill -0 $pid 2>/dev/null; then
                log_info "Stopping $component (PID: $pid)"
                kill -TERM $pid 2>/dev/null || true
                # Wait a bit for graceful shutdown
                sleep 2
                # Force kill if still running
                kill -9 $pid 2>/dev/null || true
            fi
            rm -f "$DATA_DIR/$component.pid"
        fi
    done
    
    # Clean up any remaining weed processes
    pkill -f "weed.*master\|weed.*volume\|weed.*filer\|weed.*s3\|weed.*mq" 2>/dev/null || true
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Start SeaweedFS components individually for testing.

OPTIONS:
    -h, --help              Show this help message
    -d, --data-dir DIR      Data directory (default: /tmp/seaweedfs-timestamp)
    -b, --binary PATH       Path to weed binary (default: weed)
    -v, --verbose LEVEL     Verbose level 0-3 (default: 1)
    
    --master-port PORT      Master port (default: 9333)
    --volume-port PORT      Volume port (default: 8080)
    --filer-port PORT       Filer port (default: 8888)
    --s3-port PORT          S3 port (default: 8000)
    --mq-port PORT          MQ port (default: 17777)
    
    --no-master             Don't start master
    --no-volume             Don't start volume server
    --no-filer              Don't start filer
    --with-s3               Start S3 gateway
    --with-mq               Start MQ broker
    --s3-config FILE        S3 configuration file
    
    --volume-max NUM        Max volumes (default: 100)
    --volume-size-limit MB  Volume size limit (default: 100MB)
    --filer-max-mb MB       Filer max MB (default: 64MB)
    --no-raft               Don't use Raft for master
    
    --stop                  Stop all components and exit
    --status                Show cluster status
    --no-cleanup-trap       Don't install EXIT trap (for CI use)
    
EXAMPLES:
    # Start basic cluster (master + volume + filer)
    $0
    
    # Start with S3 gateway
    $0 --with-s3 --s3-config /path/to/s3.json
    
    # Start with MQ broker
    $0 --with-mq
    
    # Start everything
    $0 --with-s3 --with-mq --s3-config /path/to/s3.json
    
    # Custom ports
    $0 --master-port 9334 --volume-port 8081 --filer-port 8889
    
    # Stop all components
    $0 --stop

ENVIRONMENT VARIABLES:
    WEED_DATA_DIR          Data directory
    WEED_BINARY           Path to weed binary
    S3_CONFIG_FILE        S3 configuration file
    
EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_usage
            exit 0
            ;;
        -d|--data-dir)
            DATA_DIR="$2"
            shift 2
            ;;
        -b|--binary)
            WEED_BINARY="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE="$2"
            shift 2
            ;;
        --master-port)
            MASTER_PORT="$2"
            shift 2
            ;;
        --volume-port)
            VOLUME_PORT="$2"
            shift 2
            ;;
        --filer-port)
            FILER_PORT="$2"
            shift 2
            ;;
        --s3-port)
            S3_PORT="$2"
            shift 2
            ;;
        --mq-port)
            MQ_PORT="$2"
            shift 2
            ;;
        --no-master)
            START_MASTER=false
            shift
            ;;
        --no-volume)
            START_VOLUME=false
            shift
            ;;
        --no-filer)
            START_FILER=false
            shift
            ;;
        --with-s3)
            START_S3=true
            shift
            ;;
        --with-mq)
            START_MQ=true
            shift
            ;;
        --s3-config)
            S3_CONFIG_FILE="$2"
            shift 2
            ;;
        --volume-max)
            VOLUME_MAX="$2"
            shift 2
            ;;
        --volume-size-limit)
            VOLUME_SIZE_LIMIT="$2"
            shift 2
            ;;
        --filer-max-mb)
            FILER_MAX_MB="$2"
            shift 2
            ;;
        --no-raft)
            USE_RAFT=false
            shift
            ;;
        --no-cleanup-trap)
            CLEANUP_ON_EXIT=false
            shift
            ;;
        --stop)
            stop_all
            exit 0
            ;;
        --status)
            show_status
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Main execution
main() {
    log_info "🚀 Starting SeaweedFS Components"
    echo "Data Directory: $DATA_DIR"
    echo "Weed Binary: $WEED_BINARY"
    echo ""
    
    # Create data directory
    mkdir -p "$DATA_DIR"
    
    # Set trap to cleanup on exit (unless disabled for CI)
    if [ "$CLEANUP_ON_EXIT" = "true" ]; then
        trap 'stop_all' EXIT INT TERM
    fi
    
    # Start components in order
    if [ "$START_MASTER" = "true" ]; then
        start_master
    fi
    
    if [ "$START_VOLUME" = "true" ]; then
        start_volume
    fi
    
    if [ "$START_FILER" = "true" ]; then
        start_filer
    fi
    
    if [ "$START_S3" = "true" ]; then
        start_s3
    fi
    
    if [ "$START_MQ" = "true" ]; then
        start_mq
    fi
    
    # Show final status
    echo ""
    show_status
    
    log_info "🎉 All requested components started successfully!"
    echo ""
    echo "Component URLs:"
    [ "$START_MASTER" = "true" ] && echo "  Master:  http://127.0.0.1:$MASTER_PORT"
    [ "$START_VOLUME" = "true" ] && echo "  Volume:  http://127.0.0.1:$VOLUME_PORT"
    [ "$START_FILER" = "true" ] && echo "  Filer:   http://127.0.0.1:$FILER_PORT"
    [ "$START_S3" = "true" ] && echo "  S3:      http://127.0.0.1:$S3_PORT"
    [ "$START_MQ" = "true" ] && echo "  MQ:      tcp://127.0.0.1:$MQ_PORT"
    echo ""
    echo "Logs are in: $DATA_DIR/"
    echo "To stop all components: $0 --stop"
}

# Run main function
main
