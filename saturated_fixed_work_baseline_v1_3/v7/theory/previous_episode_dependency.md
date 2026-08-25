# Previous-Episode Dependency Audit

`retrieve_episodes` is a state-dependent Read/Input, not a fixed episode
constant. The selector includes reference time, `last_n`, group/source filters
and optional explicit UUIDs. The ordered result and selected window are
prompt-visible projections.

The result flows into node extraction context, node resolution context, edge
extraction/resolution context, and attribute/summary extraction. The minimum
typed dependency chain is:

```
previous-episode selector --environment/order--> result window
result window --data--> node extraction/resolution
result window --data--> edge extraction/resolution
result window --data--> attribute/summary batch
each consumer --data/control--> demand and canonical request
```

Required witness/delta fields are selector identity, reference-time epoch,
window/order, projection digest and source/group filters. A change in any of
these fields reaches demand unless a local proof absorbs the complete
projection. The RED test `test_previous_episode_window_is_state_dependency_reaching_demand`
locks this contract. A frozen transcript may remove the dependency only when
its digest and all prompt-visible order are included in the witness.
