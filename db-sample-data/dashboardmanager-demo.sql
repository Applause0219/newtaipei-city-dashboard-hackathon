--
-- PostgreSQL database dump
--

-- Dumped from database version 16.4
-- Dumped by pg_dump version 16.8 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;



--
-- Data for Name: component_charts; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.component_charts (index, color, types, unit) FROM stdin;
\.


--
-- Data for Name: component_maps; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.component_maps (id, index, title, type, source, size, icon, paint, property) FROM stdin;
\.


--
-- Data for Name: components; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.components (id, index, name) FROM stdin;
\.


--
-- Data for Name: contributors; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.contributors (id, user_id, user_name, image, link, identity, description, include, created_at, updated_at) FROM stdin;
1	doit	臺北市政府資訊局	doit.png	https://doit.gov.taipei/	\N	\N	f	2024-05-09 01:58:47.164185+00	2024-05-09 01:58:47.164185+00
2	ntpc	新北市政府資訊中心	ntpc.png	https://www.imc.ntpc.gov.tw/	\N	\N	f	2024-05-09 01:58:47.164185+00	2024-05-09 01:58:47.164185+00
\.

--
-- Data for Name: dashboards; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.dashboards (id, index, name, components, icon, updated_at, created_at) FROM stdin;
\.


COPY public.groups (id, name, is_personal, create_by) FROM stdin;
1	public	f	\N
2	taipei	f	\N
3	metrotaipei	f	\N
\.


--
-- Data for Name: issues; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.issues (id, title, user_name, user_id, context, description, decision_desc, status, updated_by, created_at, updated_at) FROM stdin;
\.


ALTER TABLE public.query_charts OWNER TO postgres;
\.
TRUNCATE TABLE public.query_charts RESTART IDENTITY CASCADE;
COPY public.query_charts (index, history_config, map_config_ids, map_filter, time_from, time_to, update_freq, update_freq_unit, source, short_desc, long_desc, use_case, links, contributors, created_at, updated_at, query_type, query_chart, query_history, city) FROM stdin;
\.


COPY public.dashboard_groups (dashboard_id, group_id) FROM stdin;
\.

--
-- TOC entry 3377 (class 0 OID 0)
-- Dependencies: 222
-- Name: dashboards_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.dashboards_id_seq', (SELECT COALESCE(MAX(id), 0) FROM public.dashboards), true);


--
-- TOC entry 3378 (class 0 OID 0)
-- Dependencies: 224
-- Name: groups_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.groups_id_seq', (SELECT COALESCE(MAX(id), 4) FROM public.groups), true);


-- Completed on 2024-02-16 10:38:44 UTC

--
-- PostgreSQL database dump complete
