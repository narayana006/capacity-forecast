# capacity-forecast

Forecast when services will run out of CPU, memory, disk or quota from existing Prometheus metrics.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A command-line tool.

**The problem it solves:** Capacity problems are predictable weeks ahead, but few teams have time to do the forecasting.

## Who it is for

Service owners and platform teams responsible for capacity.

## How it will work

1. You point the tool at Prometheus.
2. It reads usage and limits for CPU, memory, disk and quotas and projects the trend forward.
3. It lists the resources that will run out first, with an estimated date and a confidence range.

## Planned features

Unticked items are not built yet.

- [ ] Pulls usage and limit metrics from Prometheus
- [ ] Trend and seasonality forecast per resource
- [ ] Days-until-exhaustion estimate with a confidence range
- [ ] Ranked report of the resources closest to their limits
- [ ] Optional alert rules generated from the forecast

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
capacity-forecast --prometheus http://prom:9090 --horizon 60d
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
