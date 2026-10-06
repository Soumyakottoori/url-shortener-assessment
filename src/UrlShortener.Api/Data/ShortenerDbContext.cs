using Microsoft.EntityFrameworkCore;

namespace UrlShortener.Api.Data;

public sealed class ShortenerDbContext(DbContextOptions<ShortenerDbContext> options) : DbContext(options)
{
    public DbSet<LinkEntity> Links => Set<LinkEntity>();
    public DbSet<ClickEntity> Clicks => Set<ClickEntity>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<LinkEntity>(entity =>
        {
            entity.HasKey(link => link.Code);
            entity.Property(link => link.Code).HasMaxLength(32);
            entity.Property(link => link.Url).HasMaxLength(2048).IsRequired();
        });
        modelBuilder.Entity<ClickEntity>(entity =>
        {
            entity.HasKey(click => new { click.Code, click.Day });
            entity.Property(click => click.Code).HasMaxLength(32);
            entity.Property(click => click.Day).HasMaxLength(10);
        });
    }
}

public sealed class LinkEntity
{
    public string Code { get; set; } = "";
    public string Url { get; set; } = "";
    public long Created { get; set; }
    public long? Expires { get; set; }
    public bool Disabled { get; set; }
}

public sealed class ClickEntity
{
    public string Code { get; set; } = "";
    public string Day { get; set; } = "";
    public long Count { get; set; }
}

public enum LinkResolutionStatus
{
    Found,
    NotFound,
    ExpiredOrDisabled
}

public sealed record LinkResolution(LinkResolutionStatus Status, string? Url);

public sealed class LinkCodeConflictException : Exception;