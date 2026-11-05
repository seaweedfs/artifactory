package cli

import "testing"

func TestResolveTopologyValue(t *testing.T) {
	vars := map[string]string{
		"ssh_key": "C:\\work\\dev_server\\testdev_key",
		"user":    "testdev",
	}

	tests := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "spaced template",
			in:   "{{ ssh_key }}",
			want: "C:\\work\\dev_server\\testdev_key",
		},
		{
			name: "compact template",
			in:   "{{user}}",
			want: "testdev",
		},
		{
			name: "literal preserved",
			in:   "192.168.1.184",
			want: "192.168.1.184",
		},
		{
			name: "unknown preserved",
			in:   "{{ missing }}",
			want: "{{ missing }}",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := resolveTopologyValue(tt.in, vars); got != tt.want {
				t.Fatalf("got %q, want %q", got, tt.want)
			}
		})
	}
}
