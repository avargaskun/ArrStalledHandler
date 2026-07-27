# Changelog

## [1.3.0](https://github.com/avargaskun/ArrStalledHandler/compare/v1.2.2...v1.3.0) (2026-07-27)


### Features

* add optional YAML configuration with per-tag stall policies ([#9](https://github.com/avargaskun/ArrStalledHandler/issues/9)) ([e569259](https://github.com/avargaskun/ArrStalledHandler/commit/e56925962e0033127d0ed2e959466ff8da6ef108))

## [1.2.2](https://github.com/avargaskun/ArrStalledHandler/compare/v1.2.1...v1.2.2) (2026-07-26)


### Bug Fixes

* send the health server's 404 response ([cfb630d](https://github.com/avargaskun/ArrStalledHandler/commit/cfb630d16d656b91182740f4cd652b9086583854))

## [1.2.1](https://github.com/avargaskun/ArrStalledHandler/compare/v1.2.0...v1.2.1) (2026-07-26)


### Bug Fixes

* handle instance-suffixed service names and config/db crash paths ([bcdf4c6](https://github.com/avargaskun/ArrStalledHandler/commit/bcdf4c6d752d5d824842e244916a2972f3f8896a))

## [1.2.0](https://github.com/avargaskun/ArrStalledHandler/compare/v1.1.3...v1.2.0) (2026-07-25)


### Features

* add /ping health-check HTTP server on :9898 ([ebc40d0](https://github.com/avargaskun/ArrStalledHandler/commit/ebc40d0a379091746331ce8cf729b707179dedb4))
* qBittorrent integration for stalled torrent handling ([92b22ed](https://github.com/avargaskun/ArrStalledHandler/commit/92b22ed57ae441b2c69c3a429734d1cb64d88741))


### Bug Fixes

* **qbittorrent:** accept WebUI auth-bypass (204/empty body) login response ([39c8c52](https://github.com/avargaskun/ArrStalledHandler/commit/39c8c528ca94157d061ade4796900428f37b1564))
