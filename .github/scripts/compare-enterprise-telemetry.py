#!/usr/bin/env python3
import hashlib
import json
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen


def require(condition, message):
    if not condition:
        raise ValueError(message)


def is_count(value):
    return type(value) is int and value >= 0


def validate_chart(kind, chart):
    require(isinstance(chart, dict), kind + ' response must be an object')
    dates = chart.get('dates')
    require(isinstance(dates, list) and dates and all(isinstance(day, str) for day in dates),
            kind + ' dates must be a nonempty list of strings')

    def series(item, label):
        values = chart_values(item, label)
        require(isinstance(values, list) and len(values) == len(dates) and all(map(is_count, values)),
                kind + ' ' + label + ' must have one count per date')

    if kind == 'metrics':
        series(chart, 'server_counts')
        series(chart, 'disk_usage')
        return

    if kind == 'cluster-sizes':
        rows, key, columns = 'clusters', 'cluster_id', ('disk', 'servers')
        totals = ('cluster_count', 'total_disk', 'total_servers')
    else:
        rows, key, columns = 'versions', 'version', ('clusters',)
        totals = ('total_clusters',)

    require(all(is_count(chart.get(field)) for field in totals), kind + ' totals are missing or invalid')
    entries = chart.get(rows)
    require(isinstance(entries, list), kind + ' ' + rows + ' must be a list')
    for entry in entries:
        require(isinstance(chart_values(entry, key), str) and entry[key], kind + ' series label is missing')
        for column in columns:
            series(entry, column)
    if 'other' in chart:
        other = chart['other']
        require(is_count(chart_values(other, 'count')), kind + ' other count is missing or invalid')
        for column in columns:
            series(other, column)


def chart_values(item, field):
    return item.get(field) if isinstance(item, dict) else None


def compare():
    legacy = 'http://127.0.0.1:8353'
    stage = 'http://127.0.0.1:8354'

    def fetch(base, path):
        try:
            with urlopen(base + path, timeout=40) as response:
                return response.read()
        except OSError:
            raise ValueError('Cannot fetch ' + path.partition('?')[0]) from None

    def get(base, path):
        return json.loads(fetch(base, path))

    old_html = fetch(legacy, '/dashboard')
    require(old_html == fetch(stage, '/dashboard'), 'Dashboard HTML differs')
    print('Dashboard HTML matches, SHA-256 {}'.format(hashlib.sha256(old_html).hexdigest()))

    old_stats = get(legacy, '/api/stats')
    new_stats = get(stage, '/api/stats')
    totals = ('total_instances', 'active_instances', 'confirmed_instances')
    for stats in (old_stats, new_stats):
        require(all(is_count(stats.get(key)) for key in totals), 'Unexpected stats response')
        for key in ('versions', 'os_distribution', 'confirmed_by_days'):
            require(isinstance(stats.get(key), dict), 'Missing stats field ' + key)
    print('Live and staged cluster counts:', [tuple(stats[k] for k in totals) for stats in (old_stats, new_stats)])

    for kind, query in (('metrics', 'days=30'), ('cluster-sizes', 'days=30&limit=20'),
                        ('versions', 'days=30&limit=8')):
        path = '/api/' + kind + '?' + query
        old, new = get(legacy, path), get(stage, path)
        validate_chart(kind, old)
        validate_chart(kind, new)
        require(old['dates'] == new['dates'], 'Chart date axes differ for ' + kind)
        print('{} returned {} dates on both ports, current data {}'.format(
            kind, len(new['dates']), 'matches' if old == new else 'differs'))
        if kind == 'cluster-sizes':
            clusters = [cluster['cluster_id'] for cluster in new['clusters'][:8]]

    today = int(time.time()) // 86400
    require(clusters, 'No clusters available to compare')
    for cluster in clusters:
        path = '/api/history?' + urlencode({'cluster_id': cluster, 'days': 14})

        def completed(host):
            return [s for s in get(host, path)['samples'] if today - 10 <= s['ts'] // 86400 < today]

        old, new = completed(legacy), completed(stage)
        require(old and old == new, 'Completed-day history differs for a sampled cluster')
    print('Completed-day history matches for {} clusters'.format(len(clusters)))


def self_test():
    metrics = {'dates': ['2026-11-23'], 'server_counts': [1], 'disk_usage': [2]}
    versions = {'dates': ['2026-11-23'], 'total_clusters': 1,
                'versions': [{'version': '4.55', 'clusters': [1]}]}
    validate_chart('metrics', metrics)
    validate_chart('versions', versions)
    missing_disk = dict(metrics)
    del missing_disk['disk_usage']
    for kind, incomplete in (('metrics', missing_disk),
                             ('metrics', dict(metrics, disk_usage=[2, 3])),
                             ('versions', dict(versions, versions=[{'version': '4.55'}]))):
        try:
            validate_chart(kind, incomplete)
        except ValueError:
            continue
        raise AssertionError(kind + ' accepted a missing series with a valid date axis')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test()
    else:
        try:
            compare()
        except (KeyError, TypeError, ValueError) as exc:
            sys.exit(str(exc))
