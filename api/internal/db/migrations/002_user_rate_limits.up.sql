CREATE TABLE user_rate_limits (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    rpm INT NOT NULL CHECK (rpm > 0),
    rph INT NOT NULL CHECK (rph > 0),
    rpd INT NOT NULL CHECK (rpd > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
