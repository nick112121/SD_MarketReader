'use strict';
// Stub of Tradovate's tools/predef for unit testing.
// paramSpecs.number(default, step, min) → returns the default value (tests just want defaults).
module.exports = {
    paramSpecs: {
        number: (def, step, min) => def,
        bool:   (def) => def,
        enum:   (def) => def,
    },
    plotters: {
        custom: (fn) => ({ type: 'custom', render: fn }),
    },
    studies: {},
};
