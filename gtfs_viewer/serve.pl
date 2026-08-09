#!/usr/bin/perl
use strict;
use warnings;
use IO::Socket::INET;
use File::Basename;

my $dir = shift || ".";
my $port = shift || 8000;
my $server = IO::Socket::INET->new(LocalAddr => "0.0.0.0", LocalPort => $port,
    Proto => "tcp", Listen => 16, ReuseAddr => 1) or die "cannot listen: $!";
print "Serving $dir on http://localhost:$port  (Ctrl-C to stop)\n";

my %mime = (
  html => "text/html; charset=utf-8",
  txt  => "text/plain; charset=utf-8",
  js   => "text/javascript",
  css  => "text/css",
  json => "application/json",
  png  => "image/png",
  svg  => "image/svg+xml",
);

while (my $c = $server->accept()) {
  $c->autoflush(1);
  my $req = <$c>;
  next unless defined $req;
  my ($method, $path) = $req =~ /^(\w+)\s+(\S+)/;
  $path //= "/";
  $path =~ s/\?.*//;
  $path = "/viewer.html" if $path eq "/" or $path eq "/index.html";
  $path =~ s{^/+}{};
  my $full = "$dir/$path";
  my $resp;
  if ($method ne "GET") {
    $resp = "405 Method Not Allowed";
    $c->print("HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
  } elsif (-f $full) {
    open my $fh, "<", $full or $resp = "500 open failed";
    if ($fh) {
      binmode $fh;
      local $/;
      my $data = <$fh>;
      my $ext = ($full =~ /\.([a-zA-Z0-9]+)$/) ? lc($1) : "";
      my $type = $mime{$ext} || "application/octet-stream";
      $c->print("HTTP/1.1 200 OK\r\nContent-Type: $type\r\nContent-Length: " . length($data) . "\r\nConnection: close\r\n\r\n");
      $c->print($data);
      close $fh;
    } else {
      $c->print("HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    }
  } else {
    $c->print("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
  }
  close $c;
}
