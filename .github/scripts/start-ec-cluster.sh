#!/bin/bash

# SeaweedFS EC Test Cluster Startup Script
# Usage: ./start-ec-cluster.sh [options]
#
# This script starts a SeaweedFS cluster optimized for EC (Erasure Coding) testing:
# - 1 Master server
# - 6 Volume servers (minimum for EC 10+4 shard distribution)
# - Each volume server on a different rack for realistic testing

set -e

# Default configuration
MASTER_PORT=${MASTER_PORT:-9333}
VOLUME_PORT_START=${VOLUME_PORT_START:-8080}
NUM_VOLUME_SERVERS=${NUM_VOLUME_SERVERS:-6}
VOLUME_SIZE_LIMIT_MB=${VOLUME_SIZE_LIMIT_MB:-10}
VOLUME_MAX_PER_SERVER=${VOLUME_MAX_PER_SERVER:-10}

DATA_DIR=${WEED_DATA_DIR:-"/tmp/seaweedfs-ec-test-$(date +%s)"}
WEED_BINARY=${WEED_BINARY:-"weed"}
VERBOSE=${VERBOSE:-1}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Function to wait for a service to be ready
wait_for_service() {
    local service_name=$1
    local host=$2
    local port=$3
    local max_attempts=${4:-30}
    
    log_step "Waiting for $service_name on $host:$port..."
    
    for i in $(seq 1 $max_attempts); do
        if curl -s "http://$host:$port/" > /dev/null 2>&1 || curl -s "http://$host:$port/status" > /dev/null 2>&1; then
            log_info "✅ $service_name is ready"
            return 0
        fi
        echo "  Waiting for $service_name... ($i/$max_attempts)"
        sleep 2
    done
    
    log_error "❌ $service_name failed to start within $(($max_attempts * 2)) seconds"
    return 1
}

# Function to wait for gRPC port
wait_for_grpc() {
    local service_name=$1
    local host=$2
    local port=$3
    local max_attempts=${4:-30}
    
    log_step "Waiting for $service_name gRPC on $host:$port..."
    
    for i in $(seq 1 $max_attempts); do
        if nc -z $host $port 2>/dev/null; then
            log_info "✅ $service_name gRPC is ready"
            return 0
        fi
        echo "  Waiting for $service_name gRPC... ($i/$max_attempts)"
        sleep 2
    done
    
    log_error "❌ $service_name gRPC failed to start"
    return 1
}

# Function to start master server
start_master() {
    log_step "Starting SeaweedFS Master Server for EC testing..."
    
    local master_dir="$DATA_DIR/master"
    mkdir -p "$master_dir"
    
    nohup $WEED_BINARY -v $VERBOSE master \
        -port=$MASTER_PORT \
        -mdir="$master_dir" \
        -volumeSizeLimitMB=$VOLUME_SIZE_LIMIT_MB \
        -ip="127.0.0.1" \
        -ip.bind="0.0.0.0" \
        -peers="none" \
        > "$master_dir/master.log" 2>&1 &
    
    echo $! > "$DATA_DIR/master.pid"
    
    if ! wait_for_service "Master" "127.0.0.1" "$MASTER_PORT" 30; then
        log_error "Master failed to start. Log:"
        tail -50 "$master_dir/master.log" || echo "Could not read master log"
        return 1
    fi
    
    # Wait for gRPC port
    local grpc_port=$((MASTER_PORT + 10000))
    if ! wait_for_grpc "Master" "127.0.0.1" "$grpc_port" 30; then
        return 1
    fi
}

# Function to start volume servers (6 servers for EC distribution)
start_volume_servers() {
    log_step "Starting $NUM_VOLUME_SERVERS SeaweedFS Volume Servers for EC testing..."
    
    for i in $(seq 0 $((NUM_VOLUME_SERVERS - 1))); do
        local volume_dir="$DATA_DIR/volume$i"
        local port=$((VOLUME_PORT_START + i))
        local rack="rack$i"
        
        mkdir -p "$volume_dir"
        
        log_info "Starting Volume Server $i on port $port (rack: $rack)..."
        
        nohup $WEED_BINARY -v $VERBOSE volume \
            -port=$port \
            -dir="$volume_dir" \
            -max=$VOLUME_MAX_PER_SERVER \
            -mserver="127.0.0.1:$MASTER_PORT" \
            -ip="127.0.0.1" \
            -ip.bind="0.0.0.0" \
            -dataCenter="dc1" \
            -rack="$rack" \
            > "$volume_dir/volume.log" 2>&1 &
        
        echo $! > "$DATA_DIR/volume$i.pid"
    done
    
    # Wait for all volume servers to be ready
    for i in $(seq 0 $((NUM_VOLUME_SERVERS - 1))); do
        local port=$((VOLUME_PORT_START + i))
        if ! wait_for_service "Volume Server $i" "127.0.0.1" "$port" 30; then
            log_error "Volume Server $i failed to start. Log:"
            tail -50 "$DATA_DIR/volume$i/volume.log" || echo "Could not read volume log"
            return 1
        fi
    done
    
    # Extra wait for volume servers to register with master
    log_step "Waiting for volume servers to register with master..."
    sleep 5
}

# Function to start multi-disk volume servers (for disk-aware EC tests)
start_multidisk_volume_servers() {
    log_step "Starting $NUM_VOLUME_SERVERS multi-disk Volume Servers for EC testing..."
    
    local disks_per_server=4
    
    for i in $(seq 0 $((NUM_VOLUME_SERVERS - 1))); do
        local port=$((VOLUME_PORT_START + i))
        local rack="rack$i"
        
        # Create multiple disk directories per server
        local disk_dirs=""
        local max_volumes=""
        
        for d in $(seq 0 $((disks_per_server - 1))); do
            local disk_dir="$DATA_DIR/server${i}_disk${d}"
            mkdir -p "$disk_dir"
            
            if [ -z "$disk_dirs" ]; then
                disk_dirs="$disk_dir"
                max_volumes="5"
            else
                disk_dirs="$disk_dirs,$disk_dir"
                max_volumes="$max_volumes,5"
            fi
        done
        
        log_info "Starting multi-disk Volume Server $i on port $port (rack: $rack, disks: $disks_per_server)..."
        
        nohup $WEED_BINARY -v $VERBOSE volume \
            -port=$port \
            -dir="$disk_dirs" \
            -max="$max_volumes" \
            -mserver="127.0.0.1:$MASTER_PORT" \
            -ip="127.0.0.1" \
            -ip.bind="0.0.0.0" \
            -dataCenter="dc1" \
            -rack="$rack" \
            > "$DATA_DIR/server${i}_logs/volume.log" 2>&1 &
        
        mkdir -p "$DATA_DIR/server${i}_logs"
        echo $! > "$DATA_DIR/volume$i.pid"
    done
    
    # Wait for all volume servers to be ready
    for i in $(seq 0 $((NUM_VOLUME_SERVERS - 1))); do
        local port=$((VOLUME_PORT_START + i))
        if ! wait_for_service "Volume Server $i" "127.0.0.1" "$port" 30; then
            return 1
        fi
    done
    
    # Extra wait for volume servers to register with master
    log_step "Waiting for multi-disk volume servers to register with master..."
    sleep 8
}

# Function to show cluster status
show_status() {
    log_step "Checking EC cluster status..."
    
    echo "=== Cluster Status ==="
    curl -s "http://127.0.0.1:$MASTER_PORT/cluster/status" | jq . 2>/dev/null || curl -s "http://127.0.0.1:$MASTER_PORT/cluster/status"
    
    echo ""
    echo "=== Directory Status ==="
    curl -s "http://127.0.0.1:$MASTER_PORT/dir/status" | head -30
    
    echo ""
    echo "=== EC Test Cluster Info ==="
    echo "Master: http://127.0.0.1:$MASTER_PORT"
    for i in $(seq 0 $((NUM_VOLUME_SERVERS - 1))); do
        local port=$((VOLUME_PORT_START + i))
        echo "Volume Server $i: http://127.0.0.1:$port (rack$i)"
    done
    
    echo ""
    echo "=== Running Processes ==="
    if [ -f "$DATA_DIR/master.pid" ]; then
        echo "Master PID: $(cat $DATA_DIR/master.pid)"
    fi
    for i in $(seq 0 $((NUM_VOLUME_SERVERS - 1))); do
        if [ -f "$DATA_DIR/volume$i.pid" ]; then
            echo "Volume $i PID: $(cat $DATA_DIR/volume$i.pid)"
        fi
    done
}

# Function to stop all components
stop_all() {
    log_step "Stopping all EC cluster components..."
    
    # Stop volume servers
    for i in $(seq $((NUM_VOLUME_SERVERS - 1)) -1 0); do
        if [ -f "$DATA_DIR/volume$i.pid" ]; then
            local pid=$(cat "$DATA_DIR/volume$i.pid")
            if kill -0 $pid 2>/dev/null; then
                log_info "Stopping Volume Server $i (PID: $pid)"
                kill -TERM $pid 2>/dev/null || true
            fi
            rm -f "$DATA_DIR/volume$i.pid"
        fi
    done
    
    # Wait a bit for graceful shutdown
    sleep 2
    
    # Stop master
    if [ -f "$DATA_DIR/master.pid" ]; then
        local pid=$(cat "$DATA_DIR/master.pid")
        if kill -0 $pid 2>/dev/null; then
            log_info "Stopping Master (PID: $pid)"
            kill -TERM $pid 2>/dev/null || true
        fi
        rm -f "$DATA_DIR/master.pid"
    fi
    
    # Force kill any remaining processes
    pkill -f "weed.*master\|weed.*volume" 2>/dev/null || true
    
    log_info "EC cluster stopped"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Start a SeaweedFS cluster optimized for EC (Erasure Coding) testing.

OPTIONS:
    -h, --help              Show this help message
    -d, --data-dir DIR      Data directory (default: /tmp/seaweedfs-ec-test-timestamp)
    -b, --binary PATH       Path to weed binary (default: weed)
    -n, --num-servers NUM   Number of volume servers (default: 6)
    
    --master-port PORT      Master port (default: 9333)
    --volume-port PORT      Starting volume port (default: 8080)
    --volume-size-limit MB  Volume size limit (default: 10MB)
    
    --multidisk             Start multi-disk volume servers (4 disks each)
    
    --stop                  Stop all components and exit
    --status                Show cluster status

EXAMPLES:
    # Start basic EC test cluster (6 volume servers)
    $0
    
    # Start with 8 volume servers
    $0 --num-servers 8
    
    # Start multi-disk cluster for disk-aware EC tests
    $0 --multidisk
    
    # Stop cluster
    $0 --stop

ENVIRONMENT VARIABLES:
    WEED_DATA_DIR          Data directory
    WEED_BINARY            Path to weed binary
    NUM_VOLUME_SERVERS     Number of volume servers (default: 6)

EOF
}

# Parse arguments
MULTIDISK=false

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
        -n|--num-servers)
            NUM_VOLUME_SERVERS="$2"
            shift 2
            ;;
        --master-port)
            MASTER_PORT="$2"
            shift 2
            ;;
        --volume-port)
            VOLUME_PORT_START="$2"
            shift 2
            ;;
        --volume-size-limit)
            VOLUME_SIZE_LIMIT_MB="$2"
            shift 2
            ;;
        --multidisk)
            MULTIDISK=true
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
    log_info "🚀 Starting SeaweedFS EC Test Cluster"
    echo "Data Directory: $DATA_DIR"
    echo "Weed Binary: $WEED_BINARY"
    echo "Volume Servers: $NUM_VOLUME_SERVERS"
    echo "Multi-disk: $MULTIDISK"
    echo ""
    
    # Create data directory
    mkdir -p "$DATA_DIR"
    
    # Start master
    start_master
    
    # Start volume servers
    if [ "$MULTIDISK" = "true" ]; then
        start_multidisk_volume_servers
    else
        start_volume_servers
    fi
    
    # Show final status
    echo ""
    show_status
    
    log_info "🎉 EC Test Cluster started successfully!"
    echo ""
    echo "EC Testing Info:"
    echo "  - EC requires at least 14 servers for full 10+4 distribution"
    echo "  - This cluster has $NUM_VOLUME_SERVERS servers across $NUM_VOLUME_SERVERS racks"
    echo "  - Shards will be distributed across available servers"
    echo ""
    echo "To stop the cluster: $0 --stop"
    echo "Logs are in: $DATA_DIR/"
}

# Run main function
main

