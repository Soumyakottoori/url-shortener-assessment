using System.Net;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;
using UrlShortener.Api;
using Xunit;

namespace UrlShortener.Api.Tests;

public sealed class ApiTests : IDisposable
{
    private readonly HttpClient client;
    private readonly ApiFactory factory;

    public ApiTests()
    {
        factory = new ApiFactory();
        client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
    }

    public void Dispose()
    {
        client.Dispose();
        factory.Dispose();
    }

    [Fact]
    public async Task Administration_requires_authentication()
    {
        var response = await client.PostAsJsonAsync("/api/links", new { url = "https://example.com" });

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Create_redirect_and_stats_track_clicks()
    {
        var create = await CreateLink("https://example.com", "demo1");
        Assert.Equal(HttpStatusCode.Created, create.StatusCode);

        var redirect = await client.GetAsync("/r/demo1");
        Assert.Equal(HttpStatusCode.Redirect, redirect.StatusCode);
        Assert.Equal("https://example.com/", redirect.Headers.Location?.ToString());

        var stats = await GetStats("demo1");
        Assert.NotNull(stats);
        Assert.Equal(1, stats!.TotalClicks);
    }

    [Fact]
    public async Task Expired_link_returns_gone()
    {
        await CreateLink("https://example.com", "expire1", 1);
        await Task.Delay(TimeSpan.FromSeconds(2.1));

        var response = await client.GetAsync("/r/expire1");

        Assert.Equal(HttpStatusCode.Gone, response.StatusCode);
    }

    [Theory]
    [InlineData("javascript:alert(1)")]
    [InlineData("http://127.0.0.1")]
    [InlineData("http://10.1.1.1")]
    [InlineData("https://user:pass@example.com")]
    [InlineData("https://localhost")]
    public async Task Invalid_destination_urls_are_rejected(string url)
    {
        var response = await SendAdmin(HttpMethod.Post, "/api/links", new { url });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Concurrent_redirects_preserve_click_count()
    {
        await CreateLink("https://example.com", "count1");
        var requests = Enumerable.Range(0, 40).Select(_ => client.GetAsync("/r/count1"));
        var responses = await Task.WhenAll(requests);

        Assert.All(responses, response => Assert.Equal(HttpStatusCode.Redirect, response.StatusCode));
        var stats = await GetStats("count1");
        Assert.Equal(40, stats!.TotalClicks);
    }

    [Fact]
    public async Task Admin_rate_limit_returns_too_many_requests()
    {
        var responses = new List<HttpResponseMessage>();
        for (var index = 0; index < 61; index++)
            responses.Add(await SendAdmin(HttpMethod.Get, "/api/links/missing/stats"));

        Assert.Equal(HttpStatusCode.NotFound, responses[0].StatusCode);
        Assert.Equal(HttpStatusCode.TooManyRequests, responses[^1].StatusCode);
    }

    private async Task<HttpResponseMessage> CreateLink(string url, string alias, long? ttlSeconds = null) =>
        await SendAdmin(HttpMethod.Post, "/api/links", new { url, alias, ttl_seconds = ttlSeconds });

    private async Task<LinkStats?> GetStats(string code)
    {
        var response = await SendAdmin(HttpMethod.Get, "/api/links/" + code + "/stats");
        return await response.Content.ReadFromJsonAsync<LinkStats>();
    }

    private async Task<HttpResponseMessage> SendAdmin(HttpMethod method, string path, object? body = null)
    {
        using var request = new HttpRequestMessage(method, path);
        request.Headers.Add("X-Test-Admin", "true");
        if (body is not null)
            request.Content = JsonContent.Create(body);
        return await client.SendAsync(request);
    }

    private sealed record LinkStats(string Code,
        [property: JsonPropertyName("total_clicks")] long TotalClicks,
        IReadOnlyDictionary<string, long> Daily);
}

public sealed class ApiFactory : WebApplicationFactory<Program>
{
    private readonly string databaseName = "UrlShortenerTests_" + Guid.NewGuid().ToString("N");

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Development");
        builder.UseSetting("URLSHORTENER_CONNECTION_STRING",
            "Server=(localdb)\\MSSQLLocalDB;Database=" + databaseName + ";Trusted_Connection=True;TrustServerCertificate=True");
        builder.ConfigureServices(services =>
        {
            services.AddAuthentication(options =>
            {
                options.DefaultAuthenticateScheme = TestAuthenticationHandler.TestScheme;
                options.DefaultChallengeScheme = TestAuthenticationHandler.TestScheme;
            }).AddScheme<AuthenticationSchemeOptions, TestAuthenticationHandler>(
                TestAuthenticationHandler.TestScheme, _ => { });
        });
    }

}

public sealed class TestAuthenticationHandler(
    IOptionsMonitor<AuthenticationSchemeOptions> options,
    Microsoft.Extensions.Logging.ILoggerFactory logger,
    System.Text.Encodings.Web.UrlEncoder encoder)
    : AuthenticationHandler<AuthenticationSchemeOptions>(options, logger, encoder)
{
    public const string TestScheme = "Test";

    protected override Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        if (!Request.Headers.ContainsKey("X-Test-Admin"))
            return Task.FromResult(AuthenticateResult.NoResult());

        var identity = new ClaimsIdentity([new Claim(ClaimTypes.Name, "test-admin"),
            new Claim(ClaimTypes.Role, "urlshortener.admin")], TestScheme);
        return Task.FromResult(AuthenticateResult.Success(new AuthenticationTicket(new ClaimsPrincipal(identity), TestScheme)));
    }
}
