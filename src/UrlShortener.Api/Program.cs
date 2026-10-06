using System.Threading.RateLimiting;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Microsoft.OpenApi.Models;
using Microsoft.IdentityModel.Tokens;
using UrlShortener.Api;
using UrlShortener.Api.Data;
using UrlShortener.Api.Middleware;
using UrlShortener.Api.Services;
using UrlShortener.Api.Swagger;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls(Environment.GetEnvironmentVariable("ASPNETCORE_URLS") ?? "http://127.0.0.1:8080");
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 8192);
var connectionString = builder.Configuration["URLSHORTENER_CONNECTION_STRING"];
if (string.IsNullOrWhiteSpace(connectionString))
{
    if (!builder.Environment.IsDevelopment())
        throw new InvalidOperationException("URLSHORTENER_CONNECTION_STRING must be configured outside Development");
    connectionString = "Server=(localdb)\\MSSQLLocalDB;Database=UrlShortener;Trusted_Connection=True;TrustServerCertificate=True";
}
builder.Services.AddDbContextFactory<ShortenerDbContext>(options => options.UseSqlServer(connectionString,
    sql => sql.EnableRetryOnFailure(5, TimeSpan.FromSeconds(10), null)));
builder.Services.AddSingleton<InMemoryWriteLock>();
builder.Services.AddScoped<ILinkDataProvider, InMemoryLinkDataProvider>();
builder.Services.AddScoped<ILinkService, LinkService>();
var adminRole = Environment.GetEnvironmentVariable("JWT_ADMIN_ROLE") ?? "urlshortener.admin";
var roleClaimType = Environment.GetEnvironmentVariable("JWT_ROLE_CLAIM") ?? "role";
builder.Services.AddControllers()
    .ConfigureApiBehaviorOptions(options =>
    {
        options.InvalidModelStateResponseFactory = _ =>
            new BadRequestObjectResult(new ErrorResponse("Invalid request"));
    });
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(options =>
{
    options.SwaggerDoc("v1", new OpenApiInfo { Title = "URL Shortener API", Version = "v1" });
    options.AddSecurityDefinition("Bearer", new OpenApiSecurityScheme
    {
        Name = "Authorization",
        Type = SecuritySchemeType.Http,
        Scheme = "bearer",
        BearerFormat = "JWT",
        In = ParameterLocation.Header,
        Description = "Paste a JWT created by dotnet user-jwts."
    });
    options.OperationFilter<AuthorizeOperationFilter>();
});
var authentication = builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme);
if (builder.Environment.IsDevelopment())
{
    authentication.AddJwtBearer();
}
else
{
    var jwtAuthority = Environment.GetEnvironmentVariable("JWT_AUTHORITY");
    var jwtAudience = Environment.GetEnvironmentVariable("JWT_AUDIENCE");
    if (string.IsNullOrWhiteSpace(jwtAuthority) || string.IsNullOrWhiteSpace(jwtAudience))
        throw new InvalidOperationException("JWT_AUTHORITY and JWT_AUDIENCE must be configured outside Development");

    authentication.AddJwtBearer(options =>
    {
        options.Authority = jwtAuthority;
        options.Audience = jwtAudience;
        options.RequireHttpsMetadata = true;
        options.MapInboundClaims = false;
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer = true,
            ValidateAudience = true,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            ClockSkew = TimeSpan.FromMinutes(1),
            RoleClaimType = roleClaimType
        };
        options.Events = new JwtBearerEvents
        {
            OnChallenge = context =>
            {
                context.HandleResponse();
                context.Response.StatusCode = StatusCodes.Status401Unauthorized;
                return context.Response.WriteAsJsonAsync(new ErrorResponse("Unauthorized"));
            },
            OnForbidden = context =>
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                return context.Response.WriteAsJsonAsync(new ErrorResponse("Forbidden"));
            }
        };
    });
}
builder.Services.AddAuthorization(options =>
    options.AddPolicy("AdminOnly", policy => policy.RequireAuthenticatedUser().RequireRole(adminRole)));
builder.Services.AddRateLimiter(options =>
{
    options.AddPolicy("admin", context => RateLimitPartition.GetFixedWindowLimiter(
        context.User.Identity?.IsAuthenticated == true ? "authenticated-admins" : "unauthenticated",
        _ => new FixedWindowRateLimiterOptions
        {
            PermitLimit = 60,
            Window = TimeSpan.FromMinutes(1),
            QueueLimit = 0,
            AutoReplenishment = true
        }));
    options.OnRejected = async (context, _) =>
    {
        context.HttpContext.Response.StatusCode = StatusCodes.Status429TooManyRequests;
        await context.HttpContext.Response.WriteAsJsonAsync(new ErrorResponse("Admin rate limit exceeded"));
    };
});

var app = builder.Build();
app.UseMiddleware<ApiExceptionMiddleware>();
if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}
app.UseRouting();
app.UseAuthentication();
app.UseRateLimiter();
app.UseAuthorization();
await using (var scope = app.Services.CreateAsyncScope())
    await scope.ServiceProvider.GetRequiredService<ShortenerDbContext>().Database.MigrateAsync();

app.MapControllers();
app.Run();

public partial class Program { }