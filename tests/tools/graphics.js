'use strict';
// Stub of Tradovate's tools/graphics for unit testing.
// du()/px() are unit conversions — tests just need pass-through numbers.
// op() is a small expression builder — return a token shape.
module.exports = {
    du: (n) => n,
    px: (n) => n,
    op: (a, sym, b) => ({ a, sym, b }),
};
